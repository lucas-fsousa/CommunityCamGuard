"""Explicit, single-run native source ownership; not a production route or scheduler.

The server caller owns run() and external resource quotas. One dedicated consumer
thread owns the decoder/MP4 collector. No auto-login retry, per-viewer encoder,
fallback, physical actions or capabilities are inferred by this adapter.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass

from ...media.fragmented_mp4 import FragmentedMp4
from ...media.hevc_decoder import HevcDecoder
from ...media.mp4_fanout import Mp4Fanout, StreamClosedError, Subscription
from .p2p.contracts import P2PProbeError
from .p2p.video_consumer import VideoConsumer
from .p2p.video_delivery import VideoDelivery
from .shared_read_session import SharedReadResult, SharedReadSession


@dataclass(frozen=True, slots=True)
class NativeVideoResult:
    reason: str
    session: SharedReadResult


class NativeVideoSource:
    def __init__(self, camera_id: str, device_id: str):
        self._session = SharedReadSession(camera_id, device_id)
        self._delivery = VideoDelivery()
        self._stop = threading.Event()
        self._failed = threading.Event()
        self._cleanup_failed = threading.Event()
        self._claim = threading.Lock()
        self._started = False
        self._hub_lock = threading.Lock()
        self._hub: Mp4Fanout | None = None
        self._output: FragmentedMp4 | None = None  # Consumer-thread confined.
        self._thread: threading.Thread | None = None

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

    def _decoder(self) -> HevcDecoder:
        if self._stop.is_set():
            raise StreamClosedError("native video stopping")
        self._retire_hub()
        output = FragmentedMp4()
        decoder = HevcDecoder(output=output)
        self._output = output
        with self._hub_lock:
            self._hub = Mp4Fanout()
            if self._stop.is_set():
                self._hub.close()
        return decoder

    def _consume(self) -> None:
        consumer = VideoConsumer(self._delivery, factory=self._decoder)
        try:
            while not self._stop.is_set():
                consumer.step()
                if consumer.closed or self._stop.is_set():
                    break
                if self._output is not None:
                    while (chunk := self._output.take()) is not None:
                        with self._hub_lock:
                            if self._hub is not None:
                                self._hub.publish(chunk)
                self._stop.wait(0.002)
        except StreamClosedError:
            if not self._stop.is_set():
                self._failed.set()
        except BaseException:
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
            result = self._session.run(video_delivery=self._delivery,
                cancelled=lambda: self._stop.is_set() or cancelled())
        finally:
            self.stop()
            if self._thread.ident is not None:
                self._thread.join(timeout=2)
                if self._thread.is_alive():
                    raise P2PProbeError("native video consumer shutdown unconfirmed")
            if self._cleanup_failed.is_set():
                raise P2PProbeError("native video decoder shutdown unconfirmed")
        return NativeVideoResult("consumer_failed" if self._failed.is_set() else result.reason, result)
