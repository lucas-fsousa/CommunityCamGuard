"""Restart availability is read-only, driver-neutral and not a cleanup receipt."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from _native_runtime_fixture import SyntheticSource
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import access_keys, auth
from backend.app import main as application
from backend.app.api import media
from backend.app.db import connect, registry, source_policy
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceRegistry
from backend.app.media.source_status import describe_sources


def camera_selection(mode="legacy_rtsp", phase="active"):
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03", name="private name")
    source_policy.init_db()
    if mode != "legacy_rtsp" or phase != "active":
        pending = source_policy.begin_change(camera.camera_id, mode, expected_revision=0)
        if phase == "active":
            source_policy.activate(camera.camera_id, expected_revision=pending.revision)
        elif phase == "blocked":
            source_policy.block(camera.camera_id, expected_revision=pending.revision)
    return camera


def test_legacy_availability_preserves_default_without_claiming_frames_or_writing():
    camera = camera_selection()
    result = describe_sources([camera.camera_id])[camera.camera_id]
    assert result == {"mode": "legacy_rtsp", "phase": "active", "revision": 0, "status": "legacy", "available": True}
    with closing(connect()) as conn:
        assert conn.execute("SELECT count(*) FROM media_source_policy").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["owned_rtsp", "native"])
def test_active_persisted_intent_without_runtime_requires_recovery_and_never_mutates(mode):
    camera = camera_selection(mode)
    before = source_policy.read_for([camera.camera_id])
    for _ in range(2):
        result = describe_sources([camera.camera_id], owned=OwnedIngressBindings(), native=SourceRegistry())[camera.camera_id]
        assert result["status"] == "runtime_missing" and result["available"] is False
    assert source_policy.read_for([camera.camera_id]) == before


@pytest.mark.parametrize("phase", ["transition", "blocked"])
@pytest.mark.parametrize("mode", ["legacy_rtsp", "owned_rtsp", "native"])
def test_transition_and_blocked_intent_never_advertise_fallback(mode, phase):
    camera = camera_selection(mode, phase)
    result = describe_sources([camera.camera_id])[camera.camera_id]
    assert result["status"] == phase and result["available"] is False


def test_ready_owned_binding_is_required_not_just_a_persisted_flag(tmp_path):
    camera = camera_selection("owned_rtsp")
    worker = OwnedRtspIngress(camera.camera_id, "rtsp://127.0.0.1:9/unused", binary=tmp_path / "absent")
    worker._process = Mock(pid=12345)  # never signaled/launched
    worker._url = "rtsp://127.0.0.1:12345/ingress_" + "a" * 32
    owned = OwnedIngressBindings()
    owned.attach(worker)
    assert describe_sources([camera.camera_id], owned=owned)[camera.camera_id]["available"] is True
    owned.withdraw(camera.camera_id)
    assert describe_sources([camera.camera_id], owned=owned)[camera.camera_id]["status"] == "runtime_missing"


@pytest.mark.parametrize("mode", ["native", "legacy_rtsp", "owned_rtsp"])
def test_native_reservation_reports_actual_phase_without_opening_readers(mode):
    camera = camera_selection(mode)
    sources, source = SourceRegistry(), SyntheticSource(cleanup_error=True, ignore_cancel=True)
    with ThreadPoolExecutor(max_workers=1) as executor:
        run = executor.submit(sources.run, camera.camera_id, domain="private/account/domain",
                              factory=lambda: source, cancelled=lambda: False)
        try:
            assert source.entered.wait(1)
            status = describe_sources([camera.camera_id], native=sources)[camera.camera_id]
            assert status["status"] == ("running" if mode == "native" else "runtime_conflict")
            assert status["available"] is (mode == "native")
            assert source.hub.reader_count == 0
            sources.stop(camera.camera_id)
            stopping = describe_sources([camera.camera_id], native=sources)[camera.camera_id]
            assert stopping["available"] is False
            assert stopping["status"] == ("stopping" if mode == "native" else "runtime_conflict")
        finally:
            source.finish.set()
            with pytest.raises(SourceCleanupUnconfirmed):
                run.result(timeout=2)
    assert sources.states() == {camera.camera_id: "blocked"}
    assert describe_sources([camera.camera_id], native=sources)[camera.camera_id]["available"] is False
    encoded = json.dumps(describe_sources([camera.camera_id], native=sources))
    assert "private" not in encoded and camera.mac not in encoded and "rtsp://" not in encoded


def test_corrupt_intent_fails_closed():
    camera = camera_selection("native")
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE media_source_policy SET phase='broken' WHERE camera_id=?", (camera.camera_id,))
    result = describe_sources([camera.camera_id])[camera.camera_id]
    assert result["phase"] == "blocked" and result["available"] is False


@pytest.mark.parametrize("access", ["anonymous", "primary", "live", "ptz"])
def test_manifest_preserves_live_authorization_and_recovery_metadata(access):
    camera = camera_selection("native")
    app = FastAPI()
    app.include_router(media.router)
    app.state.media = SimpleNamespace(wait_healthy=lambda **_: True)
    with TestClient(app) as client:
        if access == "primary":
            client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        elif access != "anonymous":
            key = access_keys.create(access_keys.CreateKey(label="test", expires_at=None, permissions=(access,)))
            client.cookies.set(auth.COOKIE_NAME, auth.issue_temporary_token(key.secret))
        response = client.get("/api/media/streams")
    assert response.status_code == {"anonymous": 401, "ptz": 403}.get(access, 200)
    if response.status_code == 200:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["healthy"] is True  # hub health is not source readiness
        assert response.json()["source_states"][camera.camera_id]["status"] == "runtime_missing"
        assert "private name" not in response.text and camera.mac not in response.text


def test_status_read_failure_is_503_not_an_empty_allow_all_manifest(monkeypatch):
    camera_selection()
    app = FastAPI()
    app.include_router(media.router)
    monkeypatch.setattr(media, "describe_sources", Mock(side_effect=RuntimeError("private SQLite path")))
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        response = client.get("/api/media/streams")
    assert response.status_code == 503 and "private" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("mode", ["owned_rtsp", "native"])
def test_two_real_lifespans_do_not_restore_persisted_intent_or_start_workers(mode, monkeypatch):
    camera = camera_selection(mode)
    before = source_policy.read_for([camera.camera_id])
    monkeypatch.setattr(application.Go2rtc, "wait_healthy", lambda *_, **__: False)
    for _ in range(2):
        with TestClient(application.app) as client:
            client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
            response = client.get("/api/media/streams")
            assert response.status_code == 200
            assert response.json()["source_states"][camera.camera_id]["status"] == "runtime_missing"
            assert not application.app.state.native_runtime._executor._threads
            assert not application.app.state.owned_runtime._executor._threads
    assert source_policy.read_for([camera.camera_id]) == before
