"""Single-owner broker route OPEN/relay/CLOSE; no sockets, login or media decoder."""

import secrets
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .account_output_budget import AccountOutputBudget
from .broker_sequences import BrokerSequences
from .contracts import CallingAttempt, CertifiedNode, OnlineDevice
from .crypto import gute_mode1_xor_checksum
from .media_protocol import build_av_init
from .relay_selection import select_relay
from .rendezvous_protocol import build_calling_request, build_route_hangup
from .video_startup_645 import with_preconnect_definition


@dataclass(frozen=True, slots=True)
class MediaRouteSpec:
    """Trusted exact-device admission, not a public capability grant."""
    access_id: int = field(repr=False)
    device: OnlineDevice = field(repr=False)


@dataclass(frozen=True, slots=True)
class MediaRoutePacket:
    kind: str
    wire: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class MediaRouteResult:
    opened: bool
    close_attempted: bool
    broker_release: bool


class MediaRouteJob:
    """A returned packet is attempted; never replay OPEN after an uncertain send.

    Sole reader feeds already decrypted/validated broker frames. cancel() is the
    only cross-thread method. Owner must finish media-channel AVCLOSE separately
    before cancelling this broker route; broker receipt alone is not media cleanup.
    """

    def __init__(self, node: CertifiedNode, sequences: BrokerSequences,
                 output: AccountOutputBudget, spec: MediaRouteSpec, *, generation: int,
                 local_ip: str, local_port: int, clock: Callable[[], float] = time.monotonic):
        if (not isinstance(spec, MediaRouteSpec) or not isinstance(spec.device, OnlineDevice)
                or not sequences.matches(node.session_id, generation)
                or type(spec.access_id) is not int or not 0 <= spec.access_id < 1 << 64
                or type(spec.device.device_id) is not int
                or not 0 < spec.device.device_id < 1 << 64 or not spec.device.status):
            raise ValueError("media route identity mismatch")
        self._reserve = output.reserve_media()
        try:
            opening, self._closing_sequence = sequences.reserve(2)
            self.attempt = CallingAttempt(secrets.randbelow(0xFFFFFF) + 1,
                                          secrets.randbits(32), secrets.token_bytes(8))
            self.metadata = with_preconnect_definition(build_av_init(self.attempt.call_id)[24:56], 3)
            self._opening = build_calling_request(node, spec.access_id, spec.device,
                local_ip, local_port, self.attempt, opening,
                request_user_data=self.metadata, connection_type=1)
            self._closing = build_route_hangup(node, spec.access_id, spec.device.device_id,
                                              self.attempt.link_id, self._closing_sequence)
        except BaseException:
            self._reserve.close()
            raise
        self._node: CertifiedNode | None = node
        self._generation, self._sequences = generation, sequences
        self._clock = clock
        self._admit_until = clock() + 1
        self._relay_until = self._close_until = 0.0
        self._cancel = threading.Event()
        self._opened = self._closing_sent = False
        self.endpoint: tuple[str, int] | None = None
        self.result: MediaRouteResult | None = None

    def cancel(self) -> None:
        self._cancel.set()

    def send_failed(self) -> None:
        self.cancel()

    def _finish(self, confirmed: bool = False) -> None:
        self.result = MediaRouteResult(self._opened, self._closing_sent, confirmed)
        if confirmed or not self._opened:
            self._reserve.close()
        self._opening = self._closing = b""
        self._node = None
        self.attempt = CallingAttempt(0, 0, b"")
        self.metadata = b""
        self.endpoint = None

    def next_packet(self) -> MediaRoutePacket | None:
        if self.result is not None:
            return None
        assert self._node is not None
        now = self._clock()
        if not self._opened:
            if (self._cancel.is_set() or now >= self._admit_until
                    or not self._sequences.matches(self._node.session_id, self._generation)):
                self._finish()
                return None
            self._reserve.charge("open", len(self._opening))
            self._opened = True
            self._relay_until = now + 4
            return MediaRoutePacket("open", self._opening)
        if self.endpoint is None and now >= self._relay_until:
            self.cancel()
        if not self._closing_sent and self._cancel.is_set():
            self._reserve.charge("close", len(self._closing))
            self._closing_sent = True
            self._close_until = now + 1.5
            return MediaRoutePacket("close", self._closing)
        if self._closing_sent and now >= self._close_until:
            self._finish()
        return None

    def observe(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> None:
        if (self.result is not None or self._node is None or not self._opened or peer != self._node.address
                or type(generation) is not int or generation != self._generation):
            return
        if not self._closing_sent:
            if self.endpoint is None and not self._cancel.is_set() and self._clock() < self._relay_until:
                self.endpoint = select_relay(plain, self._node.session_id, self.attempt.link_id)
            return
        if (self._clock() >= self._close_until or len(plain) != 32
                or plain[0] not in (0x7E, 0x7F) or plain[1] != 0xB9
                or struct.unpack_from("<H", plain, 2)[0] != 32
                or struct.unpack_from("<Q", plain, 4)[0] != self._node.session_id
                or struct.unpack_from("<I", plain, 12)[0] != self._closing_sequence
                or struct.unpack_from("<I", plain, 16)[0] != gute_mode1_xor_checksum(plain)):
            return
        flags = struct.unpack_from("<I", plain, 20)[0]
        if ((flags >> 16) & 3) == 2 and flags & (1 << 20) and not flags & (1 << 21):
            self._finish(struct.unpack_from("<H", plain, 26)[0] == 0)
