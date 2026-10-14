"""Derive scoped PTZ grants from the sole owner's correlated read completions.

Trusted internal data only: this is not a browser assertion parser or an I/O path.
The owner retains enrollment/account exclusion and online target validation.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ...db.p2p import P2PEnrollment
from .native_ptz_policy import PtzProfile
from .p2p.broker_reads import ReadCompletion, ReadRequest
from .p2p.broker_transactions import ReceiptTicket
from .p2p.contracts import P2PPropertyRead
from .p2p.ptz_authority import PtzAuthority, PtzGrant
from .p2p.ptz_protocol import DIRECTIONS, direction_supported
from .ptz_identity_policy import identity_directions

_PATHS = ("ProConst._productInfo", "ProConst._versionInfo", "ProReadonly.devInfo")


@dataclass(frozen=True, slots=True)
class VerifiedMotionScope:
    generation: int
    camera_id: str = field(repr=False)
    grant: PtzGrant = field(repr=False)
    valid_until: float

    def for_generation(self, generation: int, *,
                       clock: Callable[[], float] = time.monotonic) -> PtzGrant:
        """Consume under the same account/enrollment lock, never after re-login."""
        now = clock()
        if (type(generation) is not int or generation != self.generation
                or not math.isfinite(now) or not 0 < self.valid_until - now <= 10):
            raise ValueError("motion evidence expired or belongs to another generation")
        return self.grant


def grant_from_completions(
    enrollment: P2PEnrollment, *, camera_id: str, profile: PtzProfile,
    generation: int, requests: tuple[ReadRequest, ...],
    completions: tuple[ReadCompletion, ...], started_at: float,
    clock: Callable[[], float] = time.monotonic,
) -> VerifiedMotionScope:
    """Match exactly three issued reads, within one ten-second collection window.

    Requests/clock originate from the sole owner, not client metadata. Accept
    response-before-transport-ACK, but never ACK alone, errors, duplicate paths,
    cross-device/generation responses or data from another request sequence.
    No DB reads/writes, login, socket, blocking model helper or motion is performed.
    """
    now = clock()
    if (not isinstance(camera_id, str) or not camera_id or enrollment.camera_id != camera_id
            or type(generation) is not int or not 0 < generation < 1 << 64
            or type(started_at) not in (int, float) or not math.isfinite(started_at)
            or not math.isfinite(now) or not 0 <= now - started_at < 10
            or not isinstance(requests, tuple) or len(requests) != 3
            or not isinstance(completions, tuple) or len(completions) != 3
            or not isinstance(profile, PtzProfile)
            or not isinstance(profile.directions, frozenset) or not profile.directions
            or not profile.directions <= DIRECTIONS.keys()):
        raise ValueError("invalid motion evidence scope")
    device = enrollment.device_id
    if (not isinstance(device, str) or not device.isascii() or not device.isdecimal()
            or not 0 < len(device) <= 20 or not 0 < int(device) < 1 << 64):
        raise ValueError("invalid enrolled device")
    expected = {}
    sequences = set()
    for request in requests:
        if (not isinstance(request, ReadRequest) or request.path not in _PATHS
                or request.path in expected or type(request.device_id) is not int
                or request.device_id != int(device) or not isinstance(request.ticket, ReceiptTicket)
                or request.ticket.generation != generation
                or type(request.ticket.generation) is not int or request.ticket.kind != 0xB7
                or type(request.ticket.sequence) is not int
                or not 0 <= request.ticket.sequence < 1 << 32
                or request.ticket.sequence in sequences):
            raise ValueError("invalid motion evidence request")
        sequences.add(request.ticket.sequence)
        expected[request.path] = request
    observations = {}
    for completion in completions:
        if not isinstance(completion, ReadCompletion) or not isinstance(completion.request, ReadRequest):
            raise ValueError("typed camera completion required")
        request = completion.request
        if (not isinstance(request.ticket, ReceiptTicket)
                or type(request.device_id) is not int or type(request.ticket.generation) is not int
                or type(request.ticket.sequence) is not int or type(request.ticket.kind) is not int
                or request.path in observations or expected.get(request.path) != request
                or completion.status != "response" or type(completion.error_code) is not int
                or completion.error_code != 0
                or (completion.transport_result is not None
                    and (type(completion.transport_result) is not int or completion.transport_result != 0))):
            raise ValueError("motion evidence read was not successful and correlated")
        observations[request.path] = P2PPropertyRead(device, request.path, True, False,
            completion.transport_result == 0, 0, completion.value)
    reviewed = identity_directions(observations[_PATHS[0]], observations[_PATHS[1]],
        device_id=device, expected=profile.identity, reviewed=profile.directions)
    allowed = frozenset(d for d in reviewed if direction_supported(observations[_PATHS[2]].value, d))
    grant = PtzGrant(enrollment.access_id, int(device), allowed)
    PtzAuthority((grant,))  # Also rejects empty axes and malformed account identity.
    return VerifiedMotionScope(generation, camera_id, grant, started_at + 10)
