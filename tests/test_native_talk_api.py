from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.diagnostics import native_talk_api as api

PATH = "/api/internal/diagnostics/native-talk"
CAMERA = "cam_" + "a" * 24
PCM = bytes(640)


@pytest.fixture
def setup(monkeypatch):
    settings = SimpleNamespace(native_talk_diagnostic_enabled=True, native_talk_diagnostic_camera_id=CAMERA)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    calls = []

    def send(camera_id, pcm):
        calls.append((camera_id, pcm))
        return dict(status="transport_received", acknowledged_frames=1, stop_acknowledged=True)

    monkeypatch.setattr(api, "_send", send)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
    client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
    return client, settings, calls


def post(client, **kwargs):
    return client.post(PATH, content=PCM, headers={"content-type":"audio/pcm"}, **kwargs)


def test_pinned_single_use_does_not_claim_speaker_success(setup):
    client, _, calls = setup
    result = post(client)
    assert result.status_code == 200 and result.json()["speaker_verified"] is False
    assert calls == [(CAMERA, PCM)]
    assert post(client).status_code == 409 and len(calls) == 1


def test_legacy_review_is_explicit_and_consumes_the_same_one_use_guard(setup, monkeypatch):
    client, _, _ = setup
    calls = []

    def send(camera_id, pcm, *, legacy=False):
        calls.append((camera_id, pcm, legacy))
        return dict(status='transport_received', acknowledged_frames=1, stop_acknowledged=True)

    monkeypatch.setattr(api, '_send', send)
    r = client.post(PATH+'-legacy', content=PCM, headers={'content-type': 'audio/pcm'})
    assert r.status_code == 200 and r.json()['speaker_verified'] is False
    assert calls == [(CAMERA, PCM, True)]
    assert post(client).status_code == 409
    assert client.post(PATH+'-legacy', content=PCM, headers={'content-type': 'audio/pcm'}).status_code == 409


def test_negotiated_review_is_pinned_and_shares_the_single_use_guard(setup, monkeypatch):
    client, _, _ = setup
    calls = []

    def send(camera_id, pcm, *, negotiated_aac=False):
        calls.append((camera_id, pcm, negotiated_aac))
        return dict(status='transport_received', acknowledged_frames=1, stop_acknowledged=True)

    monkeypatch.setattr(api, '_send', send)
    r = client.post(PATH+'-negotiated', content=PCM, headers={'content-type': 'audio/pcm'})
    assert r.status_code == 200 and r.json()['speaker_verified'] is False
    assert calls == [(CAMERA, PCM, True)]
    assert post(client).status_code == 409
    assert client.post(PATH+'-legacy', content=PCM, headers={'content-type': 'audio/pcm'}).status_code == 409


def test_legacy_review_requires_enabled_pin_and_direct_authenticated_operator(setup):
    client, settings, calls = setup
    headers = {'content-type': 'audio/pcm'}
    settings.native_talk_diagnostic_enabled = False
    assert client.post(PATH+'-legacy', content=PCM, headers=headers).status_code == 404
    settings.native_talk_diagnostic_enabled = True
    assert client.post(PATH+'-legacy', content=PCM, headers={**headers, 'x-forwarded-for': '127.0.0.1'}).status_code == 403
    assert client.post(PATH+'-legacy', content=PCM, headers=headers, params={'protocol': 'anything'}).status_code == 400
    client.cookies.clear()
    assert client.post(PATH+'-legacy', content=PCM, headers=headers).status_code == 401
    assert not calls


def test_disabled_invalid_target_and_parameters_send_nothing(setup):
    client, settings, calls = setup
    settings.native_talk_diagnostic_enabled = False
    assert post(client).status_code == 404
    settings.native_talk_diagnostic_enabled = True
    settings.native_talk_diagnostic_camera_id = ""
    assert post(client).status_code == 409
    settings.native_talk_diagnostic_camera_id = CAMERA
    assert post(client, params={"camera_id":"another"}).status_code == 400
    assert not calls


@pytest.mark.parametrize("header", ["x-forwarded-for", "forwarded", "cf-connecting-ip"])
def test_proxy_cannot_trigger_speaker_review(setup, header):
    client, _, calls = setup
    assert client.post(PATH, content=PCM, headers={"content-type":"audio/pcm",header:"127.0.0.1"}).status_code == 403
    assert not calls


def test_missing_login_and_invalid_audio_do_not_consume_or_send(setup):
    client, _, calls = setup
    assert client.post(PATH, content=b"bad", headers={"content-type":"audio/pcm"}).status_code == 422
    assert post(client).status_code == 200
    client.cookies.clear()
    assert post(client).status_code == 401
    assert len(calls) == 1
