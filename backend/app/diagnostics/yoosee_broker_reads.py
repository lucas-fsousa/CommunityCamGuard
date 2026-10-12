"""Internal same-process read pilot; separately gated trigger, no auto-activation."""

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from ..db import p2p, registry
from ..drivers.yoosee.p2p.av_route_io import BudgetSocket
from ..drivers.yoosee.p2p.broker_account_check import run_owned_account_check
from ..drivers.yoosee.p2p.broker_read_socket import ReadSpec, run_owned_reads
from ..drivers.yoosee.p2p.camera_session import open_camera_session
from ..drivers.yoosee.p2p.contracts import ACCOUNT_IDENTITY_READ_PATHS
from ..drivers.yoosee.p2p.control_ownership import own_control_channel
from ..drivers.yoosee.p2p.session_io import local_route_ip
from ..services.camera_controls import CameraNotFound, _exclusive

IDENTITY_PATHS = ACCOUNT_IDENTITY_READ_PATHS


@dataclass(frozen=True, slots=True)
class BrokerReadObservation:
    path: str
    status: str
    transport_result: int | None
    error_code: int | None


@dataclass(frozen=True, slots=True)
class BrokerHeartbeatObservation:
    status: str
    interval_hint: int | None


@dataclass(frozen=True, slots=True)
class BrokerReadSummary:
    observations: tuple[BrokerReadObservation, ...]
    failure: str | None
    sent_datagrams: int
    received_datagrams: int
    after_observations: tuple[BrokerReadObservation, ...] = ()
    heartbeats: tuple[BrokerHeartbeatObservation, ...] = ()


def run_reviewed_broker_reads(*, camera_id: str, reviewed_camera_id: str,
                              reviewed_device_id: str,
                              with_heartbeat: bool = False,
                              cancelled: Callable[[], bool] = lambda: False) -> BrokerReadSummary:
    """Three fixed identity reads under camera+broker ownership through cleanup.

    Trusted operator target only. No discovery sweep, media rendezvous, control
    actions, credential renewal or raw values in the returned summary. Running
    in a separate process would not share the server reservations; do not do so.
    """
    if (type(with_heartbeat) is not bool or not reviewed_camera_id
            or camera_id != reviewed_camera_id or not reviewed_device_id):
        raise ValueError("broker read diagnostic requires reviewed target")
    with _exclusive(camera_id), own_control_channel():
        if registry.get_camera_by_id(camera_id) is None:
            raise CameraNotFound("camera not found")
        enrollment = p2p.get_enrollment_for_camera(camera_id)
        if (enrollment is None or enrollment.camera_id != camera_id
                or enrollment.device_id != reviewed_device_id):
            raise ValueError("broker read enrollment mismatch")
        bounded = BudgetSocket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM), cancelled)
        transferred = False
        try:
            bounded.bind(("", 0))
            node, target, _ = open_camera_session(cast(socket.socket, bounded), enrollment,
                                                 1.0, bounded.deadline)
            if str(target.device_id) != reviewed_device_id or not target.status:
                raise ValueError("broker read online target mismatch")
            specs = tuple(ReadSpec(target.device_id, path, 2.0) for path in IDENTITY_PATHS)
            if with_heartbeat:
                local_ip, local_port = local_route_ip(node.address), bounded.getsockname()[1]
                transferred = True
                check = run_owned_account_check(bounded.sock, node, 1, specs, local_ip,
                                                local_port, cancelled=cancelled)
                return BrokerReadSummary(
                    tuple(BrokerReadObservation(r.request.path, r.status, r.transport_result,
                                               r.error_code) for r in check.before),
                    check.failure, check.sent_datagrams, check.received_datagrams,
                    tuple(BrokerReadObservation(r.request.path, r.status, r.transport_result,
                                               r.error_code) for r in check.after),
                    tuple(BrokerHeartbeatObservation(h.status, h.interval_hint) for h in check.heartbeats))
            # Fresh dedicated socket, no callbacks from another generation.
            transferred = True
            result = run_owned_reads(bounded.sock, node, 1, specs, cancelled=cancelled)
            return BrokerReadSummary(tuple(BrokerReadObservation(r.request.path, r.status,
                r.transport_result, r.error_code) for r in result.completions), result.failure,
                result.sent_datagrams, result.received_datagrams)
        finally:
            if not transferred:
                bounded.close()
