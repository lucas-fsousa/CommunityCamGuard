"""Experimental fresh identity-read connection; caller owns account reservations."""

import socket
import time
from collections.abc import Callable
from typing import cast

from ....db.p2p import P2PEnrollment
from .account_read_lifecycle import ReadConnection
from .av_route_io import BudgetSocket
from .camera_session import open_camera_session
from .contracts import P2PProbeError
from .list_dns import resolve_list_hosts
from .session_io import local_route_ip


class ReadConnectFailure(P2PProbeError):
    """Opening failed, but any allocated broker socket was confirmed closed."""


def open_read_connection(enrollment: P2PEnrollment, *, camera_id: str, device_id: str,
                         cancelled: Callable[[], bool] = lambda: False) -> ReadConnection:
    """Resolve then authenticate within one original20s budget, no credential renewal.

    For AccountReadLifecycle's trusted factory only. Caller retains camera and
    shared-account reservations until worker cleanup, not merely factory return.
    Uses no cached PTZ socket and sends no action/media rendezvous commands.
    Socket and process creation retain their OS-level scheduling limitations.
    """
    if (not camera_id or not device_id or enrollment.camera_id != camera_id
            or enrollment.device_id != device_id):
        raise ValueError("read connection enrollment mismatch")
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
        node, target, _ = open_camera_session(
            cast(socket.socket, bounded), enrollment, 1.0, deadline, resolved_hosts=hosts)
        bounded.check()
        if str(target.device_id) != device_id or not target.status:
            raise P2PProbeError("read connection online target mismatch")
        local_ip, local_port = local_route_ip(node.address), bounded.getsockname()[1]
        bounded.check()
        connection = ReadConnection(node, bounded.sock, local_ip, local_port, target)
        return connection
    except BaseException as exc:
        if sock is not None:
            # A close exception must escape unchanged: it does NOT prove cleanup.
            sock.close()
        if isinstance(exc, Exception):
            raise ReadConnectFailure("read connection failed with confirmed cleanup") from None
        raise
