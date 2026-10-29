"""Explicit, single-run native source ownership; not a production route or scheduler.

The server caller owns run() and external resource quotas. One dedicated consumer
thread owns the decoder/MP4 collector. No auto-login retry, per-viewer encoder,
fallback or capabilities are inferred by this adapter. Optional motion requires
explicit driver-reviewed grants; default sources remain read-only.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ...media.archive_directories import ArchiveDirectories
from ...media.archive_names import ARCHIVE_TEMPLATE, epoch_template
from ...media.archive_spec import validate_segment_seconds
from ...media.fragmented_mp4 import FragmentedMp4, Mp4OrderError
from ...media.hevc_decoder import HevcDecoder
from ...media.mp4_fanout import Mp4Fanout, StreamClosedError, Subscription
from ...media.native_av_decoder import NativeAvDecoder
from .p2p.av_delivery import NativeAvDelivery, supported_audio
from .p2p.contracts import P2PProbeError
from .p2p.ptz_authority import PtzGrant
from .p2p.video_consumer import VideoConsumer
from .p2p.video_delivery import VideoDelivery, VideoFrame
from .shared_control_reads import publish
from .shared_read_session import SharedReadResult, SharedReadSession


@dataclass(frozen=True, slots=True)
class NativeVideoResult:
    reason: str
    session: SharedReadResult
    failure_type: str = ""


class NativeVideoSource:
    # Current driver exclusion is global, even across different cameras/accounts.
    # The generic registry must not derive an independent domain per camera.
    admission_domain = "yoosee/shared-control"

    def __init__(self, camera_id: str, device_id: str, *, delivery: VideoDelivery | None = None,
                 native_audio: bool = False, archive: Path | None = None,
                 archive_segment_seconds: int = 60,
                 saving_allowed: Callable[[], bool] | None = None,
                 motion_grants: tuple[PtzGrant, ...] = (),
                 motion_valid: Callable[[], bool] | None = None):
        if not isinstance(motion_grants, tuple):
            raise ValueError("explicit reviewed media motion grants required")
        if motion_valid is not None and (not callable(motion_valid) or not motion_grants):
            raise ValueError("motion revalidation requires an explicit grant")
        if archive is not None and not native_audio:
            raise ValueError("native archive requires validated A/V ingress")
        if archive is not None and archive.name != ARCHIVE_TEMPLATE:
            raise ValueError("timestamped UTC archive template required")
        validate_segment_seconds(archive_segment_seconds)
        if saving_allowed is not None and (archive is None or not callable(saving_allowed)):
            raise ValueError("recording policy requires an archive and callable")
        if native_audio and delivery is not None:
            raise ValueError("native A/V owns its delivery")
        if delivery is not None and (not isinstance(delivery, VideoDelivery) or delivery.closed):
            raise ValueError("fresh native delivery required")
        self._camera_id = camera_id
        self._session = (SharedReadSession(camera_id, device_id, state_reads=True,
                                          motion_grants=motion_grants, motion_valid=motion_valid)
                         if motion_grants else SharedReadSession(camera_id, device_id, state_reads=True))
        self._delivery = NativeAvDelivery() if native_audio else delivery if delivery is not None else VideoDelivery()
        self._archive = archive
        self._archive_segment_seconds = archive_segment_seconds
        self._saving_allowed = saving_allowed
        self._recording_allowed = True  # Consumer-thread confined; checked before its first step.
        self._archive_dirs = ArchiveDirectories(archive) if archive is not None else None
        self._stop = threading.Event()
        self._failed = threading.Event()
        self._cleanup_failed = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._hub_lock = threading.Lock()
        self._hub: Mp4Fanout | None = None
        self._output: FragmentedMp4 | None = None  # Consumer-thread confined.
        self._thread: threading.Thread | None = None
        self._failure_type = ""
        self._failure_site = ""
        self._mp4_expected = ""
        self._mp4_actual = ""
        self.frame_rate: int | None = None

    def diagnostics(self) -> dict[str, str | bool]:
        """Safe local failure metadata, never exception messages, media or credentials."""
        return {**self._session.diagnostics(),
                "consumer_error": self._failure_type, "consumer_site": self._failure_site,
                "consumer_mp4_expected": self._mp4_expected, "consumer_mp4_actual": self._mp4_actual,
                "audio_error": self._delivery.audio_failure if isinstance(self._delivery, NativeAvDelivery) else "",
                "consumer_alive": self._thread is not None and self._thread.is_alive(),
                "cleanup_failed": self._cleanup_failed.is_set()}

    def subscribe(self) -> Subscription:
        """Internal only: future transport must authorize/revoke each subscription."""
        with self._hub_lock:
            if self._stop.is_set() or self._hub is None:
                raise StreamClosedError("native video not ready")
            return self._hub.subscribe()

    def _retire_hub(self) -> None:
        with self._hub_lock:
            if self._hub is not None:
                self._hub.close()
                self._hub = None

    def stop(self) -> None:
        self._stop.set()
        self._session.stop()
        self._delivery.close()
        self._retire_hub()

    def _decoder(self, frame: VideoFrame) -> HevcDecoder | NativeAvDecoder:
        if self._stop.is_set():
            raise StreamClosedError("native video stopping")
        self._retire_hub()
        output = FragmentedMp4()
        self.frame_rate = frame.encoding.video_frame_rate
        decoder: HevcDecoder | NativeAvDecoder
        if isinstance(self._delivery, NativeAvDelivery):
            if not supported_audio(frame.encoding):
                raise ValueError("native AAC format not validated")
            if self._archive_dirs is not None and self._recording_allowed:
                self._archive_dirs.prepare(force=True)
            archive = (epoch_template(self._archive)
                       if self._archive is not None and self._recording_allowed else None)
            decoder = NativeAvDecoder(origin_us=frame.timestamp, output=output, archive=archive,
                                       archive_segment_seconds=self._archive_segment_seconds)
        else:
            decoder = HevcDecoder(output=output, frame_rate=self.frame_rate, live_timing=True)
        self._output = output
        with self._hub_lock:
            self._hub = Mp4Fanout()
            if self._stop.is_set():
                self._hub.close()
        return decoder

    def _apply_recording_policy(self, consumer: VideoConsumer) -> None:
        if self._saving_allowed is None:
            return
        allowed = self._saving_allowed()
        if type(allowed) is not bool:
            raise ValueError("recording policy must return a boolean")
        if allowed == self._recording_allowed:
            return
        # No replacement process until the old child's final archive/exit is confirmed.
        consumer.resynchronize()
        self._retire_hub()
        self._output = None
        self._recording_allowed = allowed

    def _consume(self) -> None:
        consumer = VideoConsumer(self._delivery, factory=self._decoder)
        try:
            while not self._stop.is_set():
                self._apply_recording_policy(consumer)
                if (self._archive_dirs is not None and self._recording_allowed
                        and consumer.decoder is not None):
                    self._archive_dirs.prepare()
                consumer.step()
                if consumer.closed or self._stop.is_set():
                    break
                decoder = consumer.decoder
                if isinstance(self._delivery, NativeAvDelivery) and isinstance(decoder, NativeAvDecoder):
                    if not decoder.pending_bytes:
                        audio = self._delivery.take_audio()
                        if audio is not None and not decoder.offer_audio(audio.payload, audio.timestamp):
                            raise ValueError("native decoder refused audio access unit")
                elif isinstance(self._delivery, NativeAvDelivery) and decoder is None:
                    # Awaiting a fresh IDR: do not accumulate pre-epoch AAC indefinitely.
                    self._delivery.take_audio()
                if self._output is not None:
                    while (chunk := self._output.take()) is not None:
                        with self._hub_lock:
                            if self._hub is not None:
                                self._hub.publish(chunk)
                self._stop.wait(0.002)
        except StreamClosedError:
            if not self._stop.is_set():
                self._failed.set()
        except BaseException as error:
            self._failure_type = type(error).__name__
            if isinstance(error, Mp4OrderError):
                self._mp4_expected, self._mp4_actual = error.expected, error.actual
            trace = error.__traceback__
            while trace is not None:
                self._failure_site = f"{trace.tb_frame.f_code.co_name}:{trace.tb_lineno}"
                trace = trace.tb_next
            self._failed.set()
        finally:
            try:
                consumer.close()
            except BaseException:
                self._failed.set()
                self._cleanup_failed.set()
            finally:
                self._output = None
                self.stop()

    def run(self, *, cancelled: Callable[[], bool]) -> NativeVideoResult:
        with self._claim:
            if self._started:
                raise ValueError("native video source already started")
            self._started = True
        try:
            stopped = self._stop.is_set() or cancelled()
        except BaseException:
            self.stop()
            raise
        if stopped:
            self.stop()
            return NativeVideoResult("stopped", SharedReadResult("stopped"))
        self._thread = threading.Thread(target=self._consume, name="native-video-consumer", daemon=True)
        try:
            self._thread.start()
            with publish(self._camera_id, self._session):
                result = self._session.run(video_delivery=self._delivery,
                    cancelled=lambda: self._stop.is_set() or cancelled())
        finally:
            self.stop()
            if self._thread.ident is not None:
                self._thread.join(timeout=3 if self._archive is not None else 2)
                if self._thread.is_alive():
                    raise P2PProbeError("native video consumer shutdown unconfirmed")
            if self._cleanup_failed.is_set():
                raise P2PProbeError("native video decoder shutdown unconfirmed")
        return NativeVideoResult("consumer_failed" if self._failed.is_set() else result.reason,
                                 result, self._failure_type)
