from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.p2p import account_read_connect as opener
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from tests.test_av_route import ENROLLMENT
from tests.test_broker_model_reply import NODE


@pytest.mark.parametrize("fault", [None, "dns", "auth", "target", "offline", "route", "interrupt", "late"])
def test_factory_preserves_budget_and_socket_ownership(monkeypatch, fault):
    now, sockets = [10.0], []
    monkeypatch.setattr(opener.time, "monotonic", lambda: now[0])
    class Socket:
        closed = 0
        def bind(self, address): pass
        def getsockname(self): return ("0.0.0.0", 1234)
        def close(self): self.closed += 1
    def create(*args):
        sock = Socket()
        sockets.append(sock)
        return sock
    def resolve(deadline, **kwargs):
        assert deadline == 30.0 and not sockets
        if fault == "dns": raise P2PProbeError("resolver")
        now[0] += 4
        return ("192.0.2.5",)
    def authenticate(sock, enrollment, timeout, deadline, **kwargs):
        assert deadline == sock.deadline == 30.0  # Not34 after DNS.
        assert kwargs["resolved_hosts"] == ("192.0.2.5",)
        if fault == "auth": raise OSError("auth")
        if fault == "interrupt": raise KeyboardInterrupt
        if fault == "late": now[0] = 31.0
        return NODE, SimpleNamespace(device_id=999 if fault == "target" else 123,
                                     status=0 if fault == "offline" else 1), 17
    def route(peer):
        if fault == "route": raise OSError("route")
        return "192.0.2.10"
    monkeypatch.setattr(opener, "resolve_list_hosts", resolve)
    monkeypatch.setattr(opener.socket, "socket", create)
    monkeypatch.setattr(opener, "open_camera_session", authenticate)
    monkeypatch.setattr(opener, "local_route_ip", route)
    if fault:
        with pytest.raises((OSError, P2PProbeError, KeyboardInterrupt)):
            opener.open_read_connection(ENROLLMENT, camera_id="cam_test", device_id="123")
        assert all(sock.closed == 1 for sock in sockets)
    else:
        result = opener.open_read_connection(ENROLLMENT, camera_id="cam_test", device_id="123")
        assert result.node is NODE and result.socket is sockets[0]
        assert result.local_port == 1234 and sockets[0].closed == 0
        result.socket.close()


def test_mismatched_enrollment_never_resolves(monkeypatch):
    monkeypatch.setattr(opener, "resolve_list_hosts", lambda *a, **k: pytest.fail("DNS"))
    with pytest.raises(ValueError):
        opener.open_read_connection(ENROLLMENT, camera_id="other", device_id="123")
