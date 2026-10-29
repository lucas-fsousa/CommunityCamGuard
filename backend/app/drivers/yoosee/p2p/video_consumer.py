"""Single-owner queue consumer with explicit decoder-epoch retirement.

Caller owns the dedicated consumer loop and must close it on cancellation. No
threads or camera connections are created here; this is not an account-loop task.
"""

from collections.abc import Callable
from typing import Protocol

from .video_delivery import VideoDelivery, VideoFrame


class Decoder(Protocol):
    @property
    def pending_bytes(self) -> int: ...
    def offer(self, payload: bytes, *, timestamp: int | None = None) -> bool: ...
    def step(self) -> None: ...
    def close(self) -> None: ...


class VideoConsumer:
    def __init__(self, delivery: VideoDelivery, *, factory: Callable[[VideoFrame], Decoder]):
        self._delivery = delivery
        self._factory = factory
        self._decoder: Decoder | None = None
        self._epoch: int | None = None
        self.closed = False

    @property
    def decoder(self) -> Decoder | None:
        """Consumer-thread-only access to the currently owned decoder."""
        return self._decoder

    def step(self) -> None:
        if self.closed:
            raise ValueError("video consumer closed")
        try:
            if self._delivery.closed:
                self.close()
                return
            if self._decoder is not None:
                self._decoder.step()
                if self._decoder.pending_bytes:
                    return
            frame = self._delivery.take()
            if frame is None:
                return
            if frame.epoch != self._epoch:
                if not frame.restart_decoder:
                    raise ValueError("missing decoder epoch boundary")
                if self._decoder is not None:
                    # Keep the reference until shutdown succeeds; no overlapping child.
                    self._decoder.close()
                    self._decoder = None
                self._decoder = self._factory(frame)
                self._epoch = frame.epoch
            elif frame.restart_decoder:
                raise ValueError("repeated decoder epoch boundary")
            if self._decoder is None or not self._decoder.offer(frame.payload, timestamp=frame.timestamp):
                raise ValueError("decoder refused access unit")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        self.closed = True
        self._delivery.close()
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None

    def resynchronize(self) -> None:
        """Consumer-thread only: retire the child before accepting a fresh epoch.

        Unlike close(), this retains the receiver/account. Failed child retirement
        retains ownership and must abort the source, never start a second child.
        """
        if self.closed:
            raise ValueError("video consumer closed")
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None
        self._epoch = None
        self._delivery.resynchronize()
