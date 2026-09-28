"""Temporary MSE protocol/source boundary, using isolated keys/registry and fake upstream."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import access_keys, auth, session_channels
from backend.app.api import media, temporary_media
from backend.app.db import registry

REQUEST = '{"type":"mse","value":"avc1.640029,mp4a.40.2,opus"}'


@pytest.fixture
def env(monkeypatch):
    registry.init_db()
    cam = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    key = access_keys.create(access_keys.CreateKey(label="Guest", expires_at=datetime.now(UTC) + timedelta(hours=1)))
    token = auth.issue_temporary_token(key.secret)
    connections, sent, closed = [], [], []
    class Upstream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        async def send(self, value):
            sent.append(value)
        def __aiter__(self):
            return self.frames()
        async def frames(self):
            yield b"synthetic MSE frame"
            await asyncio.Future()
    def connect(url, **kwargs):
        connections.append((url, kwargs))
        return Upstream()
    monkeypatch.setattr(temporary_media.websockets, "connect", connect)
    monkeypatch.setattr(session_channels, "CHECK_INTERVAL", 0.005)
    app = FastAPI(); app.include_router(media.router)
    finished = threading.Event()
    completions = []
    async def application(scope, receive, send):
        completion = None
        if scope["type"] == "websocket":
            finished.clear()
            completion = threading.Event()
            completions.append(completion)
        try:
            await app(scope, receive, send)
        finally:
            if scope["type"] == "websocket":
                finished.set()
                completion.set()
    with TestClient(application) as client:
        client.websocket_finished = finished
        client.websocket_completions = completions
        client.cookies.set(auth.COOKIE_NAME, token)
        yield client, cam, key, connections, sent, closed


@pytest.mark.parametrize("suffix", ["hd", "web"])
def test_mse_delivers_then_revocation_closes_socket_and_upstream(env, suffix):
    client, cam, key, connections, sent, closed = env
    with client.websocket_connect(f"/api/go2rtc/ws?src={cam.camera_id}_{suffix}",
                                  headers={"Origin": "http://testserver"}) as ws:
        ws.send_text(REQUEST)
        assert ws.receive_bytes() == b"synthetic MSE frame"
        access_keys.revoke(key.metadata.id)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1008
    assert len(connections) == 1 and len(sent) == 1 and closed
    assert json.loads(sent[0]) == json.loads(REQUEST)
    assert connections[0][0].endswith("src=" + cam.camera_id + "_" + suffix)
    assert connections[0][1]["max_size"] == 4 * 1024 * 1024
    assert connections[0][1]["max_queue"] == 4


def test_registered_source_still_requires_live_permission(env):
    client, cam, _key, connections, _sent, _closed = env
    key = access_keys.create(access_keys.CreateKey(label="Controls only", expires_at=None, permissions=("ptz",)))
    client.cookies.set(auth.COOKIE_NAME, auth.issue_temporary_token(key.secret))
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(
            f"/api/go2rtc/ws?src={cam.camera_id}_hd"):
        raise AssertionError("A control grant opened live media")
    assert caught.value.code == 1008 and not connections


@pytest.mark.parametrize("invalidation", ["revoke", "expire"])
def test_two_open_sessions_end_together_without_stopping_another_key(env, monkeypatch, invalidation):
    client, cam, key, _connections, _sent, _closed = env
    now = [datetime.now(UTC)]
    monkeypatch.setattr(access_keys, "_now", lambda: now[0])
    other = access_keys.create(access_keys.CreateKey(label="Independent", expires_at=None, permissions=("live",)))
    first_token = auth.issue_temporary_token(key.secret)
    second_token = auth.issue_temporary_token(key.secret)
    other_token = auth.issue_temporary_token(other.secret)
    channels, released = [], []

    class Upstream:
        async def __aenter__(self):
            self.pulse = asyncio.Event()
            self.loop = asyncio.get_running_loop()
            channels.append(self)
            return self

        async def __aexit__(self, *args):
            released.append(self)

        async def send(self, value):
            assert value == REQUEST

        def __aiter__(self):
            return self.frames()

        async def frames(self):
            yield b"initial"
            await self.pulse.wait()
            yield b"independent channel still live"
            await asyncio.Future()

    monkeypatch.setattr(temporary_media.websockets, "connect", lambda *args, **kwargs: Upstream())
    url = f"/api/go2rtc/ws?src={cam.camera_id}_hd"

    def connect(token):
        return client.websocket_connect(url, headers={"Cookie": f"{auth.COOKIE_NAME}={token}"})

    with connect(first_token) as first, connect(second_token) as second, connect(other_token) as independent:
        for ws in (first, second, independent):
            ws.send_text(REQUEST)
            assert ws.receive_bytes() == b"initial"
        if invalidation == "revoke":
            access_keys.revoke(key.metadata.id)
        else:
            now[0] = key.metadata.expires_at
        for ws in (first, second):
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_bytes()
            assert caught.value.code == 1008
        survivor = channels[-1]
        survivor.loop.call_soon_threadsafe(survivor.pulse.set)
        assert independent.receive_bytes() == b"independent channel still live"
        assert auth.verify_token(other_token)
        # TestClient cancels its ASGI future when a context exits. Wait for each
        # application's cleanup, not merely its close frame or a shared event.
        independent.close()
        assert len(client.websocket_completions) == 3
        assert all(completion.wait(1) for completion in client.websocket_completions)
    assert len(channels) == 3 and set(released) == set(channels)


@pytest.mark.parametrize("query", [
    "src=rtsp://192.0.2.1/private", "src=ffmpeg:arbitrary", "src=http://example.invalid",
    "src=cam_" + "f" * 24 + "_hd", "src=", "src=synthetic", "src={id}",
    "src={id}_sub", "src={id}_hd&src={id}_web", "src={id}_hd&mode=webrtc",
])
def test_unknown_sources_urls_raw_feeds_and_extra_parameters_rejected(env, query):
    client, cam, _key, connections, _sent, _closed = env
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(
            "/api/go2rtc/ws?" + query.replace("{id}", cam.camera_id)):
        raise AssertionError("invalid source accepted")
    assert caught.value.code == 1008 and not connections


@pytest.mark.parametrize("payload", [
    '{"type":"webrtc/offer","value":"sdp"}', '{"type":"hls","value":"avc1.640029"}',
    '{"type":"mse","value":"opus","src":"external"}', '[]', 'null', 'not json',
    '{"type":"mse","value":null}', '{"type":"mse","value":""}',
    '{"type":"mse","value":"opus,opus"}', '{"type":"mse","value":"arbitrary"}',
    '{"type":"mse","type":"mse","value":"opus"}', 'x' * 513,
])
def test_invalid_handshake_never_opens_upstream(env, payload):
    client, cam, _key, connections, _sent, _closed = env
    with client.websocket_connect(f"/api/go2rtc/ws?src={cam.camera_id}_hd") as ws:
        ws.send_text(payload)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1008
    assert not connections


def test_binary_uploads_and_second_negotiation_never_forwarded(env):
    client, cam, _key, connections, sent, closed = env
    url = f"/api/go2rtc/ws?src={cam.camera_id}_hd"
    with client.websocket_connect(url) as ws:
        ws.send_bytes(b"not MSE negotiation")
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1008
    assert not connections
    with client.websocket_connect(url) as ws:
        ws.send_text(REQUEST)
        ws.receive_bytes()
        ws.send_text('{"type":"webrtc/offer","value":"forbidden"}')
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1008
        # Wait for owned relay tasks to finish before TestClient cancels its scope.
        assert client.websocket_finished.wait(2)
    assert len(sent) == 1 and closed


@pytest.mark.parametrize("headers", [
    {"Origin": "https://other.invalid"}, {"Origin": "null"},
    {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://other.invalid", "X-Forwarded-Host": "other.invalid", "X-Forwarded-Proto": "https"},
])
def test_origin_mismatch_denied_before_acceptance(env, headers):
    client, cam, _key, connections, _sent, _closed = env
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(
            f"/api/go2rtc/ws?src={cam.camera_id}_hd", headers=headers):
        raise AssertionError("cross-origin socket accepted")
    assert caught.value.code == 1008 and not connections


def test_camera_removal_invalidates_open_stream(env, monkeypatch):
    client, cam, _key, _connections, _sent, closed = env
    with client.websocket_connect(f"/api/go2rtc/ws?src={cam.camera_id}_hd") as ws:
        ws.send_text(REQUEST); ws.receive_bytes()
        monkeypatch.setattr(registry, "get_camera_by_id", lambda _: None)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1008
    assert closed


def test_codec_contract_matches_bundled_player():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "frontend/video-rtc.js").read_text()
    for codec in temporary_media._CODECS:
        assert f"'{codec}'" in source
    assert json.loads(temporary_media.mse_request(json.dumps({
        "type": "mse", "value": ",".join(sorted(temporary_media._CODECS)),
    })))["type"] == "mse"
