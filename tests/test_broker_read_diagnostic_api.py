from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import access_keys, auth
from backend.app.diagnostics import yoosee_broker_reads_api as api
from backend.app.diagnostics.yoosee_broker_reads import BrokerReadSummary

PATH = "/api/internal/diagnostics/broker-reads"
CAMERA = "cam_" + "a" * 24


@pytest.fixture
def setup(monkeypatch):
    settings = SimpleNamespace(broker_read_diagnostic_enabled=True,
        broker_read_diagnostic_heartbeat=False,
        broker_read_diagnostic_worker=False,
        broker_read_diagnostic_camera_id=CAMERA, broker_read_diagnostic_device_id="123")
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return BrokerReadSummary((), None, 3, 3)
    monkeypatch.setattr(api, "run_reviewed_broker_reads", run)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
    client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
    return client, settings, calls


def test_fixed_target_single_use_hidden_schema(setup):
    client, _, calls = setup
    response = client.post(PATH)
    assert response.status_code == 200
    assert response.json() == dict(observations=[], failure=None, sent_datagrams=3,
                                  received_datagrams=3, after_observations=[], heartbeats=[], worker=None)
    assert calls == [dict(camera_id=CAMERA, reviewed_camera_id=CAMERA,
                         reviewed_device_id="123", with_heartbeat=False, with_worker=False)]
    assert client.post(PATH).status_code == 409
    assert len(calls) == 1
    assert PATH not in client.get("/openapi.json").json()["paths"]


def test_disabled_and_missing_target(setup):
    client, settings, calls = setup
    settings.broker_read_diagnostic_enabled = False
    assert client.post(PATH).status_code == 404
    settings.broker_read_diagnostic_enabled = True
    settings.broker_read_diagnostic_device_id = ""
    assert client.post(PATH).status_code == 409
    assert not calls


def test_heartbeat_profile_is_server_only(setup):
    client, settings, calls = setup
    assert client.post(PATH, params={"with_heartbeat": "true"}).status_code == 400
    assert not calls
    settings.broker_read_diagnostic_heartbeat = True
    assert client.post(PATH).status_code == 200
    assert calls[0]["with_heartbeat"] is True
    assert client.post(PATH).status_code == 409


def test_worker_profile_is_server_only(setup):
    client, settings, calls = setup
    assert client.post(PATH, params={"with_worker": "true"}).status_code == 400
    assert not calls
    settings.broker_read_diagnostic_worker = True
    assert client.post(PATH).status_code == 200
    assert calls[0]["with_worker"] is True
    assert client.post(PATH).status_code == 409


@pytest.mark.parametrize("kind", ["missing", "temporary", "legacy"])
def test_primary_session_required(setup, kind):
    client, _, calls = setup
    client.cookies.clear()
    if kind == "temporary":
        key = access_keys.create(access_keys.CreateKey(label="Guest", expires_at=None, permissions=("ptz",)))
        client.cookies.set(auth.COOKIE_NAME, auth.issue_temporary_token(key.secret))
    elif kind == "legacy":
        client.cookies.set(auth.COOKIE_NAME, auth._serializer().dumps({"ok": True}))
    assert client.post(PATH).status_code == (401 if kind == "missing" else 403)
    assert not calls


@pytest.mark.parametrize("header", ["forwarded", "x-forwarded-for", "x-real-ip",
    "cf-connecting-ip", "true-client-ip", "x-forwarded-host", "x-forwarded-proto", "origin"])
def test_forwarding_or_cross_origin_denied(setup, header):
    client, _, calls = setup
    assert client.post(PATH, headers={header: "https://attacker.invalid"}).status_code == 403
    assert not calls


def test_direct_lan_denied(setup):
    original, _, calls = setup
    client = TestClient(original.app, base_url="http://192.168.1.5", client=("192.168.1.6", 1234))
    client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
    assert client.post(PATH).status_code == 403
    assert not calls


@pytest.mark.parametrize("kwargs", [{"params": {"device_id": "999"}},
    {"json": {"path": "action"}}, {"headers": {"transfer-encoding": "chunked"}}])
def test_no_client_parameters(setup, kwargs):
    client, _, calls = setup
    assert client.post(PATH, **kwargs).status_code == 400
    assert not calls
    assert client.post(PATH).status_code == 200


@pytest.mark.parametrize("error,status", [(OSError, 502), (api.P2PProbeError, 502),
    (ValueError, 409), (api.ControlBusy, 409), (api.ControlChannelBusy, 409),
    (api.ControlNotReady, 409), (api.CameraNotFound, 409)])
def test_failure_sanitized_and_consumes_attempt(setup, monkeypatch, error, status):
    client, _, _ = setup
    def fail(**kwargs): raise error("private secret")
    monkeypatch.setattr(api, "run_reviewed_broker_reads", fail)
    response = client.post(PATH)
    assert response.status_code == status and "private" not in response.text
    assert client.post(PATH).status_code == 409


def test_concurrent_attempt_rejected_while_first_is_running(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    client, _, calls = setup
    entered, release = Event(), Event()
    def run(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(3)
        return BrokerReadSummary((), None, 3, 3)
    monkeypatch.setattr(api, "run_reviewed_broker_reads", run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, PATH)
        try:
            assert entered.wait(3)
            assert client.post(PATH).status_code == 409
            assert len(calls) == 1
        finally:
            release.set()
        assert future.result(timeout=3).status_code == 200
