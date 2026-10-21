"""Finite in-server acceptance of the cooperative media pump, no playback/actions."""

import time
from collections.abc import Callable

from ..db import p2p
from ..drivers.yoosee.native_ptz import reserve_read_handoff
from ..drivers.yoosee.p2p.account_read_connect import open_read_connection
from ..drivers.yoosee.p2p.account_read_worker import AccountReadWorker, WorkerSummary
from ..drivers.yoosee.p2p.contracts import P2PProbeError
from ..drivers.yoosee.p2p.media_route_job import MediaRouteSpec
from ..services.camera_controls import _exclusive


def run_reviewed_shared_media(*, camera_id: str, device_id: str,
                              cancelled: Callable[[], bool] = lambda: False) -> WorkerSummary:
    """Trusted server-configured target only; one authentication, no retry.

    Reserve the legacy account through TCP AVCLOSE, broker B9 and physical UDP
    close. Unknown cleanup poisons that reservation instead of allowing a login.
    """
    deadline = time.monotonic() + 35
    def stop() -> bool:
        return cancelled() or time.monotonic() >= deadline

    with _exclusive(camera_id), reserve_read_handoff(wait=False):
        entry = p2p.read_registered_enrollment(camera_id)
        if entry is None or entry.camera_id != camera_id or entry.device_id != device_id:
            raise ValueError("shared media enrollment mismatch")
        connection = open_read_connection(entry, camera_id=camera_id,
                                          device_id=device_id, cancelled=stop)
        transferred = False
        try:
            target = connection.target
            if target is None or str(target.device_id) != device_id or not target.status:
                raise ValueError("shared media authenticated target mismatch")
            worker = AccountReadWorker(connection.node, 1, connection.local_ip, connection.local_port)
            spec = MediaRouteSpec(entry.access_id, target)
            transferred = True
            result = worker.run(connection.socket, initial_media=spec,
                                with_media_tcp=True, cancelled=stop)
        finally:
            if not transferred:
                connection.socket.close()
        if (result.socket_close_failed
                or (result.media is not None and result.media.opened and not result.media.broker_release)
                or (result.tcp is not None and
                    (not result.tcp.close_acknowledged or result.tcp.socket_close_failed))):
            raise P2PProbeError("shared media cleanup unconfirmed; account blocked")
        return result
