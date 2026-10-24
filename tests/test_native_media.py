"""Native source -> authenticated MSE delivery, no cameras or real encoders."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from _native_fixture import FRAGMENT, INIT
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.app import access_keys, auth, session_channels
from backend.app.api import media, native_media
from backend.app.db import registry
from backend.app.media.fragmented_mp4 import Mp4Chunk
from backend.app.media.mp4_fanout import Mp4Fanout
from backend.app.media.source_registry import SourceRegistry
from backend.app.media.viewer_gate import ViewerGate

REQUEST = '{"type":"mse","value":"avc1.640029,mp4a.40.2"}'


class Source:
    def __init__(self):
        self.hub = Mp4Fanout()
        self.hub.publish(Mp4Chunk(True, INIT))
        self.hub.publish(Mp4Chunk(False, FRAGMENT))
        self.entered = threading.Event()
        self.stop = threading.Event()
        self.cleanup = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def run(self, *, cancelled):
        self.entered.set()
        try:
            while not self.stop.wait(0.005):
                if cancelled():
                    return
        finally:
            self.hub.close()
            self.cleanup.set()
            assert self.release.wait(2)

    def subscribe(self):
        return self.hub.subscribe()


@pytest.fixture
def env(monkeypatch):
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    sources, source = SourceRegistry(capacity=2), Source()
    app = FastAPI()
    app.include_router(native_media.router)
    app.include_router(media.router)
    app.state.native_sources = sources
    monkeypatch.setattr(session_channels, "CHECK_INTERVAL", 0.005)
    monkeypatch.setattr(native_media, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(media.websockets, "connect", lambda *a, **k: pytest.fail("legacy ingress opened"))
    finished = threading.Event()

    async def application(scope, receive, send):
        if scope["type"] == "websocket":
            finished.clear()
        try:
            await app(scope, receive, send)
        finally:
            if scope["type"] == "websocket":
                finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sources.run, camera.camera_id, domain="test/one-encoder",
                             factory=lambda: source, cancelled=source.stop.is_set)
        assert source.entered.wait(2)
        try:
            with TestClient(application) as client:
                client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
                yield client, camera, source, sources, app, finished
        finally:
            source.stop.set()
            source.release.set()
            future.result(timeout=3)


def start(ws):
    ws.send_text(REQUEST)
    assert ws.receive_json() == {"type": "mse", "value": 'video/mp4; codecs="avc1.42C01E"'}
    assert ws.receive_bytes() == INIT
    assert ws.receive_bytes() == FRAGMENT


def test_two_viewers_share_source_and_one_disconnect_does_not_stop_other(env):
    client, camera, source, sources, _app, finished = env
    path = f"/api/media/native/{camera.camera_id}/ws"
    with client.websocket_connect(path) as first:
        start(first)
        with client.websocket_connect(path) as second:
            start(second)
            assert source.hub.reader_count == 2
            # An extra negotiation closes only this subscriber.
            first.send_text(REQUEST)
            with pytest.raises(WebSocketDisconnect) as caught:
                first.receive_bytes()
            assert caught.value.code == 1008 and finished.wait(1)
            source.hub.publish(Mp4Chunk(False, FRAGMENT + b"next"))
            assert second.receive_bytes() == FRAGMENT + b"next"
            assert source.hub.reader_count == 1
            assert sources.state(camera.camera_id) == "running"
            # Wait for ASGI cleanup before TestClient cancels its context. A
            # received frame alone does not mean the handler has finished.
            finished.clear()
            second.send_text(REQUEST)
            with pytest.raises(WebSocketDisconnect) as caught:
                second.receive_bytes()
            assert caught.value.code == 1008 and finished.wait(1)
            assert source.hub.reader_count == 0


@pytest.mark.parametrize("reason", ["no-cookie", "no-live", "origin", "unknown-camera", "query"])
def test_denied_connections_never_subscribe(env, reason):
    client, camera, source, _sources, _app, _finished = env
    path = f"/api/media/native/{camera.camera_id}/ws"
    headers = {}
    if reason == "no-cookie":
        client.cookies.clear()
    elif reason == "no-live":
        key = access_keys.create(access_keys.CreateKey(label="PTZ", expires_at=None, permissions=("ptz",)))
        client.cookies.set(auth.COOKIE_NAME, auth.issue_temporary_token(key.secret))
    elif reason == "origin":
        headers = {"Origin": "https://other.invalid"}
    elif reason == "unknown-camera":
        path = "/api/media/native/cam_" + "f" * 24 + "/ws"
    elif reason == "query":
        path += "?url=rtsp://forbidden"
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(path, headers=headers):
        pytest.fail("unauthorized socket accepted")
    assert caught.value.code == 1008
    assert source.hub.reader_count == 0


@pytest.mark.parametrize("registry_present", [False, True])
def test_missing_scheduler_cannot_start_source_from_websocket(env, registry_present):
    client, camera, source, _sources, app, _finished = env
    if registry_present:
        app.state.native_sources = SourceRegistry()
    else:
        del app.state.native_sources
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(
            f"/api/media/native/{camera.camera_id}/ws"):
        pytest.fail("unconfigured native source accepted")
    assert caught.value.code == 1013 and source.hub.reader_count == 0


@pytest.mark.parametrize("path_kind", ["native", "alias"])
def test_drain_releases_native_readers_without_stopping_shared_source(env, path_kind):
    client, camera, source, sources, app, _finished = env
    gate = ViewerGate()
    app.state.viewer_sessions = gate
    path = (f"/api/media/native/{camera.camera_id}/ws" if path_kind == "native"
            else f"/api/go2rtc/ws?src={camera.camera_id}_hd")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with client.websocket_connect(path) as socket:
            start(socket)
            drain = pool.submit(gate.suspend, camera.camera_id, timeout=2)
            with pytest.raises(WebSocketDisconnect):
                socket.receive_bytes()
            drain.result(timeout=2)
            assert source.hub.reader_count == 0
    assert sources.state(camera.camera_id) == "running" and not source.cleanup.is_set()
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(path):
        pytest.fail("Held native source admitted a viewer")
    gate.resume(camera.camera_id)
    with client.websocket_connect(path) as socket:
        start(socket)


@pytest.mark.parametrize("suffix", ["", "_hd", "_web"])
@pytest.mark.parametrize("authentication", ["primary", "temporary"])
def test_existing_player_alias_uses_native_without_legacy_upstream(env, suffix, authentication):
    client, camera, source, _sources, _app, finished = env
    if authentication == "temporary":
        key = access_keys.create(access_keys.CreateKey(label="Live", expires_at=None, permissions=("live",)))
        client.cookies.set(auth.COOKIE_NAME, auth.issue_temporary_token(key.secret))
    with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}{suffix}") as ws:
        start(ws)
        ws.send_text(REQUEST)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1008 and finished.wait(2)
    assert source.hub.reader_count == 0


@pytest.mark.parametrize("reason", ["no-cookie", "no-live", "duplicate-source"])
def test_existing_player_alias_does_not_bypass_native_authorization(env, reason):
    client, camera, source, _sources, _app, _finished = env
    path = f"/api/go2rtc/ws?src={camera.camera_id}_hd"
    if reason == "no-cookie":
        client.cookies.clear()
    elif reason == "no-live":
        key = access_keys.create(access_keys.CreateKey(label="PTZ", expires_at=None, permissions=("ptz",)))
        client.cookies.set(auth.COOKIE_NAME, auth.issue_temporary_token(key.secret))
    else:
        path += f"&src={camera.camera_id}_web"
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(path):
        pytest.fail("native alias bypassed authority/query check")
    assert caught.value.code == 1008 and source.hub.reader_count == 0


def test_stopping_native_source_does_not_reopen_rtsp(env):
    client, camera, source, sources, _app, finished = env
    source.release.clear()
    try:
        sources.stop(camera.camera_id)
        assert source.cleanup.wait(1)
        assert camera.camera_id in sources.camera_ids()
        with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_hd") as ws:
            ws.send_text(REQUEST)
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_bytes()
            assert caught.value.code == 1013 and finished.wait(1)
    finally:
        source.release.set()


def test_uncertain_native_cleanup_does_not_reopen_rtsp(env):
    client, _camera, _source, sources, _app, finished = env
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:04")

    def failed():
        raise RuntimeError("unknown construction cleanup")

    with pytest.raises(RuntimeError):
        sources.run(camera.camera_id, domain="other", factory=failed, cancelled=lambda: False)
    assert sources.state(camera.camera_id) == "blocked"
    with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_web") as ws:
        ws.send_text(REQUEST)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1013 and finished.wait(1)


def test_manifest_activity_and_recovery_follow_claimed_native_source(env):
    client, camera, source, sources, app, _finished = env
    other = "cam_" + "f" * 24

    class Legacy:
        def wait_healthy(self, **kwargs):
            return True

        def stream_activity(self):
            return {sid: {"consumers": 1, "video_packets": 100} for sid in
                    (camera.camera_id, camera.camera_id + "_hd", camera.camera_id + "_web", other)}

        def release_live_producers(self, *args):
            pytest.fail("native recovery touched legacy source")

    app.state.media = Legacy()
    assert client.get("/api/media/streams").json()["native_sources"] == [camera.camera_id]
    assert set(client.get("/api/media/activity").json()) == {other}
    assert client.post(f"/api/media/recover/{camera.camera_id}").json() == {"ok": True}
    assert sources.state(camera.camera_id) == "running" and not source.stop.is_set()


@pytest.mark.parametrize("action", ["revoke", "expire", "remove-camera", "end-epoch"])
def test_authority_or_epoch_end_releases_subscriber_without_stopping_source(env, monkeypatch, action):
    client, camera, source, sources, _app, finished = env
    now = datetime.now(UTC)
    key = access_keys.create(access_keys.CreateKey(label="Live", expires_at=now + timedelta(hours=1),
                                                 permissions=("live",)))
    client.cookies.set(auth.COOKIE_NAME, auth.issue_temporary_token(key.secret))
    with client.websocket_connect(f"/api/media/native/{camera.camera_id}/ws") as ws:
        start(ws)
        if action == "revoke":
            access_keys.revoke(key.metadata.id)
        elif action == "expire":
            monkeypatch.setattr(access_keys, "_now", lambda: now + timedelta(hours=2))
        elif action == "remove-camera":
            registry.delete_camera(camera.mac)
        else:
            source.hub.close()
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == (1013 if action == "end-epoch" else 1008)
        assert finished.wait(2)
    assert source.hub.reader_count == 0 and sources.state(camera.camera_id) == "running"


@pytest.mark.parametrize("negotiation", ['{"type":"webrtc/offer","value":"x"}',
                                    '{"type":"mse","value":"opus"}',
                                    '{"type":"mse","type":"mse","value":"avc1.640029"}'])
def test_bad_negotiation_never_admits_reader(env, negotiation):
    client, camera, source, _sources, _app, finished = env
    with client.websocket_connect(f"/api/media/native/{camera.camera_id}/ws") as ws:
        ws.send_text(negotiation)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
        assert caught.value.code == 1008 and finished.wait(2)
    assert source.hub.reader_count == 0


def test_stalled_write_closes_only_its_reader_and_reaps_tasks(env, monkeypatch):
    _client, camera, source, sources, _app, _finished = env
    monkeypatch.setattr(native_media, "WRITE_TIMEOUT", 0.01)
    healthy = source.hub.subscribe()

    class Socket:
        def __init__(self):
            self.first = True
            self.application_state = WebSocketState.CONNECTING
            self.codes = []

        async def accept(self):
            self.application_state = WebSocketState.CONNECTED

        async def receive(self):
            if self.first:
                self.first = False
                return {"type": "websocket.receive", "text": REQUEST}
            await asyncio.Future()

        async def send_json(self, value):
            pass

        async def send_bytes(self, data):
            await asyncio.Future()

        async def close(self, code):
            self.codes.append(code)
            self.application_state = WebSocketState.DISCONNECTED

    socket = Socket()

    async def run():
        await asyncio.wait_for(native_media._stream(socket, sources, camera.camera_id), 1)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    try:
        asyncio.run(run())
        assert socket.codes == [1011]
        assert source.hub.reader_count == 1
        assert healthy.take().data == INIT and healthy.take().data == FRAGMENT
        assert sources.state(camera.camera_id) == "running"
    finally:
        healthy.close()
