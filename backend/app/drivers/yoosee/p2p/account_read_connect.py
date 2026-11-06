"""Experimental fresh identity-read connection; caller owns account reservations."""

import socket
import time
from collections.abc import Callable
from typing import cast

from ....db.p2p import P2PEnrollment
from .account_read_lifecycle import ReadConnection
from .av_route_io import BudgetSocket
from .camera_session import open_camera_session
from .contracts import OnlineDevice, P2PProbeError
from .list_dns import resolve_list_hosts
from .ptz_diagnostics import preparation_failure_reason
from .session_io import local_route_ip


class ReadConnectFailure(P2PProbeError):
    """Opening failed, but any allocated broker socket was confirmed closed."""

    def __init__(self, message: str, *, failure: Exception | None = None):
        super().__init__(message)
        self.reason = preparation_failure_reason(failure) if failure is not None else "transport_or_auth"


def open_read_connection(enrollment: P2PEnrollment, *, camera_id: str, device_id: str,
                         cancelled: Callable[[], bool] = lambda: False,
                         additional_device_ids: tuple[str, ...] = ()) -> ReadConnection:
    """Resolve then authenticate within one original20s budget, no credential renewal.

    For AccountReadLifecycle's trusted factory only. Caller retains camera and
    shared-account reservations until worker cleanup, not merely factory return.
    Uses no cached PTZ socket and sends no action/media rendezvous commands.
    Socket and process creation retain their OS-level scheduling limitations.
    """
    if (not camera_id or not device_id or enrollment.camera_id != camera_id
            or enrollment.device_id != device_id):
        raise ValueError("read connection enrollment mismatch")
    if (not isinstance(additional_device_ids, tuple)
            or any(type(identity) is not str or not identity.isascii() or not identity.isdecimal()
                   or len(identity) > 20
                   or not 0 < int(identity) < 1 << 64 or str(int(identity)) != identity
                   or identity == device_id for identity in additional_device_ids)
            or len(set(additional_device_ids)) != len(additional_device_ids)):
        raise ValueError("bounded exact additional device IDs required")
    deadline = time.monotonic() + 20.0
    sock = None
    try:
        hosts = resolve_list_hosts(deadline, cancelled=cancelled)
        if cancelled() or time.monotonic() >= deadline:
            raise P2PProbeError("read connection deadline or cancellation")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bounded = BudgetSocket(sock, cancelled)
        bounded.deadline = deadline  # DNS time is charged; never grant a new budget.
        bounded.bind(("", 0))
        inventory: list[OnlineDevice] = []
        if additional_device_ids:
            node, target, _ = open_camera_session(cast(socket.socket, bounded), enrollment,
                1.0, deadline, resolved_hosts=hosts, inventory=inventory)
        else:
            node, target, _ = open_camera_session(
                cast(socket.socket, bounded), enrollment, 1.0, deadline, resolved_hosts=hosts)
        bounded.check()
        if str(target.device_id) != device_id or not target.status:
            raise P2PProbeError("read connection online target mismatch")
        local_ip, local_port = local_route_ip(node.address), bounded.getsockname()[1]
        bounded.check()
        selected = [target]
        for identity in additional_device_ids:
            matches = [device for device in inventory if str(device.device_id) == identity]
            if len(matches) > 1:
                raise P2PProbeError("ambiguous authenticated inventory identity")
            selected.extend(matches)
        connection = ReadConnection(node, bounded.sock, local_ip, local_port, target,
                                    enrollment.access_id, tuple(selected))
        return connection
    except BaseException as exc:
        if sock is not None:
            # A close exception must escape unchanged: it does NOT prove cleanup.
            sock.close()
        if isinstance(exc, Exception):
            raise ReadConnectFailure("read connection failed with confirmed cleanup", failure=exc) from None
        raise
