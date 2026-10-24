"""Browser source-selection guards; synthetic in-memory upstream only."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import access_keys, auth, session_channels
from backend.app.api import media
from backend.app.db import registry, source_policy
from backend.app.media.browser_ingress import admit_hub, camera_for_source
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.viewer_gate import ViewerGate


@pytest.fixture
def camera():
    registry.init_db()
    return registry.upsert_camera("aa:bb:cc:dd:ee:03")


@pytest.mark.parametrize("suffix", ["", "_hd", "_web"])
def test_exact_public_aliases_only(camera, suffix):
    assert camera_for_source(camera.camera_id + suffix) == camera.camera_id
    assert camera_for_source(camera.camera_id, allow_base=False) is None


@pytest.mark.parametrize("source", ["", "cam_test_hd", "rtsp://secret@192.0.2.1/live",
                                   "ffmpeg:camera", "_sub", "_hd_web", "_web_hd", "_hd/other"])
def test_urls_and_noncanonical_aliases_never_resolve(camera, source):
    assert camera_for_source(source) is None
    if source.startswith("_"):
        assert camera_for_source(camera.camera_id + source) is None


def test_revision_change_invalidates_viewer_even_after_return_to_legacy(camera):
    viewer = admit_hub(camera.camera_id)
    assert viewer is not None and viewer.valid()
    pending = source_policy.begin_change(camera.camera_id, "legacy_rtsp", expected_revision=0)
    assert not viewer.valid() and admit_hub(camera.camera_id) is None
    source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    assert not viewer.valid()
    fresh = admit_hub(camera.camera_id)
    assert fresh is not None and fresh.valid()
    registry.delete_camera_by_id(camera.camera_id)
    assert not fresh.valid() and admit_hub(camera.camera_id) is None


@pytest.fixture(params=["primary", "temporary"])
def browser(request, camera, monkeypatch):
    token = auth.issue_token()
    if request.param == "temporary":
        key = access_keys.create(access_keys.CreateKey(label="Synthetic viewer", expires_at=None,
                                                       permissions=("live",)))
        token = auth.issue_temporary_token(key.secret)
    connections, closed = [], []

    class Upstream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        async def send(self, value):
            pass
        def __aiter__(self):
            return self.frames()
        async def frames(self):
            yield b"synthetic MP4 data"
            await asyncio.Future()

    def connect(url, **kwargs):
        connections.append(url)
        return Upstream()

    monkeypatch.setattr(media.websockets, "connect", connect)
    monkeypatch.setattr(session_channels, "CHECK_INTERVAL", 0.005)
    app = FastAPI()
    app.include_router(media.router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        yield client, camera, connections, closed


@pytest.mark.parametrize("state", ["transition", "native", "owned_rtsp", "blocked"])
def test_nonlegacy_selection_never_opens_upstream(browser, state):
    client, camera, connections, _closed = browser
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp" if state == "owned_rtsp"
                                        else "native", expected_revision=0)
    if state == "blocked":
        source_policy.block(camera.camera_id, expected_revision=pending.revision)
    elif state != "transition":
        source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            f"/api/go2rtc/ws?src={camera.camera_id}_hd"):
        raise AssertionError("Retired source reached the media hub")
    assert not connections


@pytest.mark.parametrize("change", ["transition", "removed", "storage_failure"])
def test_open_viewer_releases_upstream_on_source_invalidation(browser, change, monkeypatch):
    client, camera, connections, closed = browser
    with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_hd") as socket:
        socket.send_text('{"type":"mse","value":"avc1.640029"}')
        assert socket.receive_bytes() == b"synthetic MP4 data"
        if change == "transition":
            source_policy.begin_change(camera.camera_id, "native", expected_revision=0)
        elif change == "removed":
            registry.delete_camera_by_id(camera.camera_id)
        else:
            monkeypatch.setattr(source_policy, "read_for", Mock(side_effect=sqlite3.OperationalError("offline")))
        with pytest.raises(WebSocketDisconnect) as caught:
            socket.receive_bytes()
        assert caught.value.code == 1008
    assert len(connections) == 1 and closed


@pytest.mark.parametrize("query", ["src=rtsp://secret@192.0.2.1/live", "src=ffmpeg:other",
                                  "src={id}_hd_web", "src={id}_hd&src={id}_web",
                                  "src={id}_hd&extra=1", "src=cam_" + "f" * 24 + "_hd"])
def test_invalid_or_unknown_source_cannot_bypass_admission(browser, query):
    client, camera, connections, _closed = browser
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/api/go2rtc/ws?" + query.replace("{id}", camera.camera_id)):
        raise AssertionError("Unreviewed source reached the media hub")
    assert not connections


def test_recovery_during_transition_never_calls_legacy_hub(camera):
    release = Mock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        media=SimpleNamespace(release_live_producers=release))))
    source_policy.begin_change(camera.camera_id, "native", expected_revision=0)
    with pytest.raises(HTTPException) as caught:
        media.media_recover(camera.camera_id, request)
    assert caught.value.status_code == 409
    release.assert_not_called()


def test_owned_binding_withdrawal_closes_open_viewer_without_policy_change(browser):
    client, camera, connections, closed = browser
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=0)
    source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    worker = OwnedRtspIngress(camera.camera_id, "rtsp://127.0.0.1:9/synthetic", binary=Path(__file__))
    worker._process = Mock(pid=12345)
    worker._url = "rtsp://127.0.0.1:12345/ingress_" + "a" * 32
    bindings = OwnedIngressBindings()
    bindings.attach(worker)
    release = Mock(return_value=True)
    engine = SimpleNamespace(owned_ingress=bindings, release_live_producers=release)
    client.app.state.media = engine
    assert media.media_recover(camera.camera_id, SimpleNamespace(app=client.app)) == {"ok": True}
    assert release.call_count == 1
    with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_hd") as socket:
        socket.send_text('{"type":"mse","value":"avc1.640029"}')
        assert socket.receive_bytes() == b"synthetic MP4 data"
        bindings.withdraw(camera.camera_id)
        with pytest.raises(WebSocketDisconnect):
            socket.receive_bytes()
    assert len(connections) == 1 and closed
    with pytest.raises(HTTPException) as caught:
        media.media_recover(camera.camera_id, SimpleNamespace(app=client.app))
    assert caught.value.status_code == 409 and release.call_count == 1


def test_coordinator_drain_waits_for_primary_or_temporary_upstream_cleanup(browser):
    client, camera, connections, closed = browser
    gate = ViewerGate()
    client.app.state.viewer_sessions = gate
    with ThreadPoolExecutor(max_workers=1) as pool:
        with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_hd") as socket:
            socket.send_text('{"type":"mse","value":"avc1.640029"}')
            assert socket.receive_bytes() == b"synthetic MP4 data"
            drain = pool.submit(gate.suspend, camera.camera_id, timeout=2)
            with pytest.raises(WebSocketDisconnect):
                socket.receive_bytes()
            drain.result(timeout=2)
            assert closed
    assert source_policy.read_for((camera.camera_id,))[camera.camera_id].permits_legacy_rtsp
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            f"/api/go2rtc/ws?src={camera.camera_id}_hd"):
        raise AssertionError("A draining camera admitted another viewer")
    with pytest.raises(HTTPException) as caught:
        media.media_recover(camera.camera_id, SimpleNamespace(app=client.app))
    assert caught.value.status_code == 409 and len(connections) == 1
    gate.resume(camera.camera_id)
    with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_hd") as socket:
        socket.send_text('{"type":"mse","value":"avc1.640029"}')
        assert socket.receive_bytes() == b"synthetic MP4 data"
    assert len(connections) == 2
