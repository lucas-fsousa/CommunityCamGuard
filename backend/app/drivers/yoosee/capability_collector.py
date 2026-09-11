"""Bounded, explicit collection of Yoosee capability properties over one session.

No polling, credential renewal, AV session, write or action is performed here.
Observations are returned to a backend caller; unverified identities must not be
persisted as a certified profile or used to enable controls.
"""

from __future__ import annotations

import socket
import time

from ...db.p2p import P2PEnrollment
from .capability_evidence import CAPABILITY_PATHS
from .capability_snapshot import CapabilitySnapshot, normalize_snapshot
from .p2p.camera_session import open_camera_session
from .p2p.contracts import P2PProbeError, P2PPropertyRead
from .p2p.model_session import exchange_model_read


def collect_snapshot(enrollment: P2PEnrollment) -> CapabilitySnapshot | None:
    """Explicit backend-only collection plus normalization; no persistence or grants.

    The server samples receipt time after the bounded exchange, never from camera t.
    No raw observations or credentials are retained in the returned snapshot.
    """
    observations = collect(enrollment)
    return normalize_snapshot(
        observations,
        camera_id=enrollment.camera_id or "",
        device_id=enrollment.device_id,
        collected_at=time.time(),
    )


def collect(enrollment: P2PEnrollment) -> tuple[P2PPropertyRead, ...]:
    """Read five fixed roots sequentially within one unchanged 20-second session budget.

    Only device/session/sequence-correlated B8 responses are collected, not AA reports.
    Correlation does not prove cache freshness; a timeout stops the batch. Observations are private
    to the driver and deliberately excluded from logs and public responses.
    """

    if not enrollment.camera_id:
        raise P2PProbeError("capability collection requires a linked camera identity")
    deadline = time.monotonic() + 20.0
    observations: list[P2PPropertyRead] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", 0))
        node, target, sequence = open_camera_session(sock, enrollment, 1.0, deadline)
        if str(target.device_id) != enrollment.device_id:
            raise P2PProbeError("capability session device identity mismatch")
        for path in CAPABILITY_PATHS:
            if time.monotonic() >= deadline:
                break
            result = exchange_model_read(
                sock,
                node,
                target,
                path,
                sequence,
                1.0,
                retries=1,
                deadline=deadline,
                require_correlated_response=True,
            )
            observations.append(
                P2PPropertyRead(
                    device_id=enrollment.device_id,
                    property_path=path,
                    authenticated=True,
                    direct_handshake=False,
                    transport_acknowledged=result.transport_acknowledged,
                    error_code=result.error_code,
                    value=result.value,
                )
            )
            if result.error_code != 0:
                break
            sequence = (sequence + 1) & 0xFFFFFFFF
    return tuple(observations)
