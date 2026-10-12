from dataclasses import asdict
from types import SimpleNamespace

import pytest

from backend.app.diagnostics import yoosee_broker_reads as diagnostic
from backend.app.drivers.yoosee.p2p.broker_read_socket import ReadExchange
from backend.app.services import camera_controls
from tests.test_av_route import ENROLLMENT
from tests.test_broker_model_reply import NODE
from tests.test_native_av_broker_ownership import available_from_other_thread


@pytest.mark.parametrize("fault", [None, "missing", "enrollment", "auth", "target", "exchange"])
@pytest.mark.parametrize("heartbeat", [False, True])
def test_reviewed_pilot_owns_both_locks_and_one_socket(monkeypatch, fault, heartbeat):
    sockets, events = [], []
    class Socket:
        closed = 0
        def __init__(self, *args): sockets.append(self)
        def bind(self, address): pass
        def getsockname(self): return ("0.0.0.0", 1234)
        def close(self):
            assert not available_from_other_thread()
            self.closed += 1
    def authenticate(sock, enrollment, timeout, deadline):
        events.append("auth")
        assert not available_from_other_thread()
        with pytest.raises(camera_controls.ControlBusy):
            with camera_controls._exclusive("cam_test"):
                pass
        if fault == "auth": raise OSError("auth failure")
        return NODE, SimpleNamespace(device_id=999 if fault == "target" else 123, status=1), 17
    def exchange(sock, node, generation, specs, *local, **kwargs):
        events.append("exchange")
        assert tuple(s.path for s in specs) == diagnostic.IDENTITY_PATHS
        assert all(s.device_id == 123 for s in specs)
        assert not available_from_other_thread()
        sock.close()  # Adapter owns physical cleanup, already separately tested.
        if fault == "exchange": raise RuntimeError("adapter interrupted")
        completion = SimpleNamespace(request=SimpleNamespace(path=specs[0].path),
            status="response", transport_result=0, error_code=0,
            value={"private_identity": "must-not-escape"})
        if heartbeat:
            assert local == ("192.0.2.1", 1234)
            return SimpleNamespace(before=(completion,), after=(completion,),
                heartbeats=(SimpleNamespace(status="response", interval_hint=35),),
                failure=None, sent_datagrams=3, received_datagrams=3)
        return ReadExchange((completion,), None, 3, 3)
    monkeypatch.setattr(diagnostic.registry, "get_camera_by_id", lambda _: None if fault == "missing" else object())
    monkeypatch.setattr(diagnostic.p2p, "get_enrollment_for_camera", lambda _: None if fault == "enrollment" else ENROLLMENT)
    monkeypatch.setattr(diagnostic.socket, "socket", Socket)
    monkeypatch.setattr(diagnostic, "open_camera_session", authenticate)
    monkeypatch.setattr(diagnostic, "run_owned_reads", exchange)
    monkeypatch.setattr(diagnostic, "run_owned_account_check", exchange)
    monkeypatch.setattr(diagnostic, "local_route_ip", lambda _: "192.0.2.1")
    kwargs = dict(camera_id="cam_test", reviewed_camera_id="cam_test", reviewed_device_id="123",
                  with_heartbeat=heartbeat)
    if fault:
        with pytest.raises((ValueError, OSError, RuntimeError, camera_controls.CameraNotFound)):
            diagnostic.run_reviewed_broker_reads(**kwargs)
    else:
        result = diagnostic.run_reviewed_broker_reads(**kwargs)
        assert result.failure is None and result.sent_datagrams == 3
        assert asdict(result)["observations"] == ({"path": diagnostic.IDENTITY_PATHS[0],
            "status": "response", "transport_result": 0, "error_code": 0},)
        assert "must-not-escape" not in repr(result)
        if heartbeat:
            assert len(result.after_observations) == 1 and result.heartbeats[0].interval_hint == 35
        assert events == ["auth", "exchange"]
    assert all(sock.closed == 1 for sock in sockets)
    assert available_from_other_thread()
    if fault in {"missing", "enrollment"}: assert not sockets
    if fault in {"auth", "target"}: assert "exchange" not in events


@pytest.mark.parametrize("reviewed_camera,reviewed_device", [
    ("", "123"), ("other", "123"), ("cam_test", ""),
])
def test_unreviewed_target_never_reaches_registry(monkeypatch, reviewed_camera, reviewed_device):
    def unexpected(*args):
        pytest.fail("unreviewed target reached registry")
    monkeypatch.setattr(diagnostic.registry, "get_camera_by_id", unexpected)
    with pytest.raises(ValueError, match="reviewed target"):
        diagnostic.run_reviewed_broker_reads(camera_id="cam_test",
            reviewed_camera_id=reviewed_camera, reviewed_device_id=reviewed_device)


def test_cancelled_before_auth_closes_socket_and_releases_locks(monkeypatch):
    from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError

    events = []
    class Socket:
        def bind(self, address): pytest.fail("cancelled socket was bound")
        def close(self): events.append("closed")
    monkeypatch.setattr(diagnostic.registry, "get_camera_by_id", lambda _: object())
    monkeypatch.setattr(diagnostic.p2p, "get_enrollment_for_camera", lambda _: ENROLLMENT)
    monkeypatch.setattr(diagnostic.socket, "socket", lambda *args: Socket())
    with pytest.raises(P2PProbeError, match="cancellation"):
        diagnostic.run_reviewed_broker_reads(camera_id="cam_test",
            reviewed_camera_id="cam_test", reviewed_device_id="123", cancelled=lambda: True)
    assert events == ["closed"]
    assert available_from_other_thread()
    with camera_controls._exclusive("cam_test"):
        pass
