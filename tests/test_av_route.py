from dataclasses import replace

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import av_route
from backend.app.drivers.yoosee.p2p.av_probe import AvProbeResult
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice, P2PProbeError
from tests.test_av_probe import CALLING, CHANNEL

ENROLLMENT = P2PEnrollment("123", 9, b"private", None, "", "", "cam_test")
NODE = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
DEVICE = OnlineDevice(123, 1, False, 1, bytes(16))
RESULT = AvProbeResult(True, True, 3, 100, 100, 1, 1, 1, 0, 0)


@pytest.fixture
def env(monkeypatch):
    events = []
    class Socket:
        def bind(self, address):
            events.append("bind")
        def close(self):
            events.append("socket_close")
    monkeypatch.setattr(av_route.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(av_route, "open_camera_session", lambda *args: (NODE, DEVICE, 17))
    def calling(*args, **kwargs):
        events.append(("call", kwargs["attempt"]))
        assert kwargs["retries"] == 1
        return replace(CALLING, attempt=kwargs["attempt"], route_link_id=kwargs["attempt"].link_id)
    monkeypatch.setattr(av_route, "call_device", calling)
    def bootstrap(*args, **kwargs):
        assert kwargs == {"require_roundtrip": True}
        return CHANNEL
    monkeypatch.setattr(av_route, "open_media_channel", bootstrap)
    def probe(sock, calling, channel, **kwargs):
        assert kwargs["close_socket"] is False
        events.append("probe")
        return RESULT
    monkeypatch.setattr(av_route, "probe_av_socket", probe)
    def release(*args, **kwargs):
        assert kwargs["require_correlated_ack"] is True
        events.append(("release", args[4], args[5]))
        return True
    monkeypatch.setattr(av_route, "close_device_route", release)
    return events


def run(**kwargs):
    return av_route.probe_av_route(ENROLLMENT, camera_id="cam_test", device_id="123", **kwargs)


def test_fresh_route_release_precedes_socket_close(env):
    result = run()
    assert result.media == RESULT and result.route_release_acknowledged
    attempt = env[1][1]
    assert env == ["bind", ("call", attempt), "probe", ("release", attempt.link_id, 19), "socket_close"]


@pytest.mark.parametrize("where", ["call_device", "open_media_channel", "probe_av_socket"])
def test_ambiguous_failure_still_releases_preallocated_route(env, monkeypatch, where):
    def fail(*args, **kwargs):
        raise OSError("private details")
    monkeypatch.setattr(av_route, where, fail)
    with pytest.raises(P2PProbeError, match="probe failed") as error:
        run()
    assert "private" not in str(error.value)
    assert env[-2][0] == "release" and env[-2][2] == 19 and env[-1] == "socket_close"


def test_wrong_online_device_never_opens_or_releases_a_direct_route(env, monkeypatch):
    monkeypatch.setattr(av_route, "open_camera_session",
                        lambda *args: (NODE, replace(DEVICE, device_id=124), 17))
    with pytest.raises(P2PProbeError, match="reviewed"):
        run()
    assert env == ["bind", "socket_close"]


def test_identity_mismatch_rejected_before_socket(env):
    with pytest.raises(ValueError):
        av_route.probe_av_route(ENROLLMENT, camera_id="another", device_id="123")
    assert not env


def test_cancelled_before_start_does_not_open_socket(env):
    with pytest.raises(P2PProbeError, match="cancelled"):
        run(cancelled=lambda: True)
    assert not env


@pytest.mark.parametrize("fault", ["missing", "error"])
def test_release_failure_cannot_claim_success(env, monkeypatch, fault):
    def release(*args, **kwargs):
        if fault == "error":
            raise OSError("private")
        return False
    monkeypatch.setattr(av_route, "close_device_route", release)
    with pytest.raises(P2PProbeError, match="release receipt"):
        run()
    assert env[-1] == "socket_close"


def test_probe_does_not_use_old_av_initializer_or_audio_or_retry():
    assert not hasattr(av_route, "initialize_av_session")
    assert not hasattr(av_route, "run_with_fresh_access")
    assert not hasattr(av_route, "send_pcm_intercom")


@pytest.mark.parametrize("where,stage", [("open_camera_session", "access_session"),
                                         ("call_device", "rendezvous"),
                                         ("open_media_channel", "media_meter"),
                                         ("probe_av_socket", "av_receive_close")])
def test_failure_stage_and_cleanup_are_logged_without_exception_text(env, monkeypatch, caplog, where, stage):
    def fail(*args, **kwargs):
        raise P2PProbeError("secret token 123456 and private endpoint")
    monkeypatch.setattr(av_route, where, fail)
    with pytest.raises(P2PProbeError):
        run()
    assert f"stage={stage}" in caplog.text and "outcome=failed" in caplog.text
    assert "error_type=P2PProbeError" in caplog.text
    assert "secret" not in caplog.text and "123456" not in caplog.text and "private" not in caplog.text
    assert f"release_attempted={where != 'open_camera_session'}" in caplog.text
    assert env[-1] == "socket_close"


def test_success_log_distinguishes_av_and_release(env, caplog):
    run()
    assert "stage=route_release outcome=av_completed" in caplog.text
    assert "release_acknowledged=True" in caplog.text


@pytest.mark.parametrize("direct,meter", [(False, True), (True, False), (False, False), (True, True)])
def test_incomplete_bootstrap_is_attributed_before_av_initialization(env, monkeypatch, caplog, direct, meter):
    monkeypatch.setattr(av_route, "open_media_channel", lambda *args, **kwargs: replace(
        CHANNEL, direct_acknowledged=direct, meter_acknowledged=meter, datagrams=3,
        meter_roundtrip_confirmed=False))
    with pytest.raises(av_route.AvBootstrapError) as error:
        run()
    assert error.value.observations == dict(phase="media_meter", direct_acknowledged=direct,
                                            meter_acknowledged=meter, datagrams=3,
                                            meter_roundtrip_confirmed=False, meter_observations=())
    assert "probe" not in env
    assert "stage=media_meter" in caplog.text
    assert env[-2][0] == "release" and env[-1] == "socket_close"


def test_correlated_meter_roundtrip_does_not_require_lan_calling_receipt(env, monkeypatch):
    monkeypatch.setattr(av_route, "open_media_channel", lambda *args, **kwargs: replace(
        CHANNEL, direct_acknowledged=False))
    assert run().media == RESULT
    assert "probe" in env
