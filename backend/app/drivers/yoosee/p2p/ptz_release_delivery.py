"""Socket-free RELEASE delivery tracking for an eventual shared account reader."""

import math
import time
from collections.abc import Callable

from .contracts import CertifiedNode
from .ptz_command_reservation import ReleaseCorrelation
from .ptz_protocol import PtzReply, parse_ptz_reply


class ReleaseDelivery:
    """One pinned release, <=2 seconds/64 frames; explicit error wins over receipts.

    Construct after the first RELEASE attempt. Feed only authenticated decrypted
    frames from the sole reader. Returned replies still require bounded ACK handling.
    A positive result means delivery, never proof the motor physically stopped.
    """

    def __init__(self, node: CertifiedNode, *, generation: int, access_id: int,
                 device_id: int, correlation: ReleaseCorrelation, deadline: float,
                 clock: Callable[[], float] = time.monotonic):
        if (type(generation) is not int or generation <= 0
                or type(access_id) is not int or not 0 < access_id < 1 << 64
                or type(device_id) is not int or not 0 < device_id < 1 << 64
                or not isinstance(correlation, ReleaseCorrelation)
                or type(correlation.sequence) is not int or not 0 <= correlation.sequence < 1 << 32
                or type(correlation.message_id) is not int or not 0 <= correlation.message_id < 1 << 31
                or type(correlation.request_id) is not int or not 0 <= correlation.request_id < 1 << 32
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not 0 < deadline - clock() <= 2):
            raise ValueError("invalid release observation lifetime")
        self._node, self._generation, self._clock = node, generation, clock
        self._access, self._device, self._correlation = access_id, device_id, correlation
        self._deadline, self._frames = deadline, 0
        self._transport = self._peer = self._application = self._error = False

    @property
    def confirmed(self) -> bool:
        return not self._error and (self._application or (self._transport and self._peer))

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> PtzReply | None:
        if self._clock() >= self._deadline:
            return None
        if self._frames >= 64:
            self._error = True
            return None
        self._frames += 1
        if (peer != self._node.address or type(generation) is not int
                or generation != self._generation or not isinstance(plain, bytes)):
            return None
        ids = self._correlation
        reply = parse_ptz_reply(plain, node=self._node, access_id=self._access,
            device_id=self._device, sequence=ids.sequence, message_id=ids.message_id,
            request_id=ids.request_id)
        if reply is not None:
            self._transport |= reply.transport_receipt
            self._peer |= reply.peer_receipt
            if reply.error_code is not None:
                self._application |= reply.error_code == 0
                self._error |= reply.error_code != 0
        return reply
