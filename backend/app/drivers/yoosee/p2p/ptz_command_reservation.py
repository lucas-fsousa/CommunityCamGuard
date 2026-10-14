"""Offline typed PTZ send reservation for a future sole account worker."""

import secrets
from dataclasses import dataclass, field

from .broker_sequences import BrokerSequences
from .contracts import CertifiedNode
from .ptz_protocol import DIRECTIONS, build_ptz_request


@dataclass(frozen=True, slots=True)
class ReleaseCorrelation:
    sequence: int
    message_id: int
    request_id: int


@dataclass(slots=True, repr=False)
class ReservedPtzCommands:
    """No socket, reader, timer, capability detection or automatic release.

    One owner invokes take_* immediately before its send attempt. START is consumed
    before transport I/O, even if that I/O subsequently fails. RELEASE may use only
    the exact prebuilt datagram, <=3 attempts. The future owner must separately
    reserve wire/queue capacity and enforce the existing motion/STOP deadlines.
    """

    release_correlation: ReleaseCorrelation
    receipt_sequence: int
    _source: BrokerSequences = field(repr=False)
    _session: int = field(repr=False)
    _generation: int = field(repr=False)
    _start: bytes = field(repr=False)
    _release: bytes = field(repr=False)
    _started: bool = False
    _cancelled: bool = False
    _release_attempts: int = 0

    def cancel_start(self) -> None:
        """Cancellation before START is motion-free; after START it cannot recall it."""
        self._cancelled = True

    def take_start(self) -> bytes:
        if self._started or self._cancelled or not self._source.matches(self._session, self._generation):
            raise ValueError("PTZ START unavailable; do not replay")
        self._started = True
        return self._start

    def take_release(self) -> bytes:
        if not self._started or self._release_attempts >= 3:
            raise ValueError("PTZ RELEASE unavailable or attempts exhausted")
        self._release_attempts += 1
        # Retirement prevents new STARTs, not cleanup on the pinned old socket.
        # Never re-authenticate or send this through a replacement generation.
        return self._release


def reserve_ptz_commands(node: CertifiedNode, source: BrokerSequences, *, generation: int,
                         access_id: int, device_id: int, direction: str,
                         reviewed_directions: frozenset[str]) -> ReservedPtzCommands:
    """Caller already proved exact enrollment/device/profile, under account ownership.

    Reservation grants no camera capability. Builds both commands before exposing
    START; construction failure burns IDs but sends nothing. One extra sequence is
    reserved for a correlated application receipt, not an arbitrary command.
    """
    if (not source.matches(node.session_id, generation)
            or type(access_id) is not int or not 0 < access_id < 1 << 64
            or type(device_id) is not int or not 0 < device_id < 1 << 64
            or not isinstance(direction, str)
            or not isinstance(reviewed_directions, frozenset)
            or not reviewed_directions or not reviewed_directions <= DIRECTIONS.keys()
            or direction not in reviewed_directions):
        raise ValueError("PTZ reservation requires exact reviewed account/axis identity")
    start_sequence, release_sequence, receipt_sequence = source.reserve(3)
    release = ReleaseCorrelation(release_sequence, secrets.randbits(31), secrets.randbits(32))
    start_wire = build_ptz_request(node, access_id, device_id, direction, True, start_sequence,
                                   secrets.randbits(31), secrets.randbits(32))
    release_wire = build_ptz_request(node, access_id, device_id, direction, False,
                                     release.sequence, release.message_id, release.request_id)
    return ReservedPtzCommands(release, receipt_sequence, source, node.session_id,
                               generation, start_wire, release_wire)
