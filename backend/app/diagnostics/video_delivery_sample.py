"""Finite bounded video-queue consumer; decode only after source cleanup and join."""

import threading
import time

from ..drivers.yoosee.p2p.av_sample import MAX_FRAMES, AvVideoSample
from ..drivers.yoosee.p2p.contracts import P2PProbeError
from ..drivers.yoosee.p2p.v1_receive import V1Record
from ..drivers.yoosee.p2p.video_delivery import VideoDelivery


class VideoDeliverySample:
    """One local consumer, no socket access or audio; max2MiB/120 sampled frames.

    Never mix decoder epochs after a queue overflow. Capacity completion stops
    collection, not the source; the source retains its own short deadline. Private
    data is cleared on failure and caller-owned on success until close().
    """

    def __init__(self, delivery: VideoDelivery):
        self.delivery = delivery
        self.sample = AvVideoSample()
        self.failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="native-video-sample", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        deadline = time.monotonic() + 40
        epoch = None
        try:
            while not self._stop.is_set():
                if time.monotonic() >= deadline:
                    raise ValueError("consumer deadline")
                frame = self.delivery.take()
                if frame is None:
                    if self.delivery.closed:
                        return
                    self._stop.wait(0.005)
                    continue
                if epoch is None:
                    if not frame.restart_decoder:
                        raise ValueError("missing initial decoder epoch")
                    epoch = frame.epoch
                elif frame.epoch != epoch or frame.restart_decoder:
                    raise ValueError("consumer epoch interrupted")
                self.sample.consume(V1Record(encoding=frame.encoding, video=frame.payload,
                                             video_timestamp=frame.timestamp))
                if self.sample.frames >= MAX_FRAMES:
                    return
        except Exception:
            self.failed = True
            self.sample.close()

    def join(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise P2PProbeError("video consumer shutdown unconfirmed")

    def close(self) -> None:
        self.join()
        self.sample.close()
