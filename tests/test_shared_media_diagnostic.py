from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.app.diagnostics import yoosee_shared_media as module
from backend.app.drivers.yoosee.p2p.account_read_worker import WorkerSummary
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from backend.app.drivers.yoosee.p2p.cooperative_tcp_media import TcpMediaResult
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteResult


@pytest.mark.parametrize("fault", [None, "enrollment", "target", "broker", "tcp", "socket"])
def test_reviewed_shared_media_ownership(monkeypatch, fault):
    calls = []
    entry = SimpleNamespace(camera_id="camera", device_id="123", access_id=7)
    target = SimpleNamespace(device_id=999 if fault == "target" else 123, status=1)
    sock = SimpleNamespace(close=lambda: calls.append("close"))
    connection = SimpleNamespace(target=target, socket=sock, node=None, local_ip="127.0.0.1", local_port=1)
    monkeypatch.setattr(module, "_exclusive", lambda _: nullcontext())
    monkeypatch.setattr(module, "reserve_read_handoff", lambda **_: nullcontext())
    monkeypatch.setattr(module.p2p, "read_registered_enrollment", lambda _: None if fault == "enrollment" else entry)
    def connect(*args, **kwargs):
        calls.append("auth")
        assert not kwargs["cancelled"]()
        return connection
    monkeypatch.setattr(module, "open_read_connection", connect)
    result = WorkerSummary("media_complete", 1, 1, 0, fault == "socket",
                           media=MediaRouteResult(True, True, fault != "broker"),
                           tcp=TcpMediaResult(True, fault != "tcp", False, 12, 0, 1))
    class Worker:
        def __init__(self, *args): pass
        def run(self, socket, **kwargs):
            assert socket is sock and kwargs["with_media_tcp"]
            assert kwargs["initial_media"].device is target
            calls.append("worker")
            socket.close()
            return result
    monkeypatch.setattr(module, "AccountReadWorker", Worker)
    if fault:
        with pytest.raises(ValueError if fault in {"enrollment", "target"} else P2PProbeError):
            module.run_reviewed_shared_media(camera_id="camera", device_id="123")
    else:
        assert module.run_reviewed_shared_media(camera_id="camera", device_id="123") == result
    assert calls == ([] if fault == "enrollment" else ["auth", "close"] if fault == "target"
                     else ["auth", "worker", "close"])
