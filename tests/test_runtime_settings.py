"""Isolated settings persistence, authorization, validation and consumer wiring."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app import auth, config, runtime_settings
from backend.app.api import media, settings
from backend.app.db import runtime_settings as store
from backend.app.recording import playback


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(settings.router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        yield client


def test_defaults_patch_reset_and_persistence(client, monkeypatch):
    monkeypatch.setenv("GRID_HD_MAX_CAMERAS", "2")
    config.get_settings.cache_clear()
    response = client.get("/api/settings")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["values"] == {"grid_hd_max_cameras": 2, "playback_cache_mb": 2048}
    result = client.patch("/api/settings", json={"revision": 0, "changes": {
        "grid_hd_max_cameras": 4, "playback_cache_mb": 512,
    }}, headers={"Origin": "http://testserver"})
    assert result.status_code == 200 and result.json()["revision"] == 1
    assert result.headers["cache-control"] == "no-store"
    config.get_settings.cache_clear()  # No process-local override cache; fresh resolution from DB.
    assert runtime_settings.grid_hd_limit() == 4
    assert runtime_settings.playback_cache_limit_mb() == 512
    assert config.get_settings().grid_hd_max_cameras == 2  # Never mutate bootstrap settings.
    result = client.patch("/api/settings", json={"revision": 1, "changes": {"grid_hd_max_cameras": None}})
    assert result.json()["values"]["grid_hd_max_cameras"] == 2
    assert result.json()["overrides"] == {"playback_cache_mb": 512}
    assert "secret" not in str(result.json()).lower()


@pytest.mark.parametrize("changes", [
    {}, {"grid_hd_max_cameras": True}, {"grid_hd_max_cameras": "2"},
    {"grid_hd_max_cameras": -1}, {"grid_hd_max_cameras": 65},
    {"playback_cache_mb": -1}, {"playback_cache_mb": 65537},
    {"playback_cache_mb": 1.5}, {"dashboard_secret_key": "injected"},
    {"recording_retention_days": 1}, {"db_path": "/tmp/other"},
    {"grid_hd_max_cameras": 2, "host": "0.0.0.0"},
])
def test_invalid_patch_has_no_partial_write(client, changes):
    response = client.patch("/api/settings", json={"revision": 0, "changes": changes})
    assert response.status_code == 422
    assert store.read() == (0, {})


def test_stale_revision_does_not_overwrite_another_tab(client):
    assert client.patch("/api/settings", json={"revision": 0, "changes": {"playback_cache_mb": 0}}).status_code == 200
    assert client.patch("/api/settings", json={"revision": 0, "changes": {"playback_cache_mb": 1}}).status_code == 409
    assert runtime_settings.playback_cache_limit_mb() == 0


def test_concurrent_writers_only_one_commits():
    store.read()
    barrier = threading.Barrier(2)
    def write(value):
        barrier.wait(timeout=3)
        try:
            store.write(0, {"grid_hd_max_cameras": value})
            return "saved"
        except store.RevisionConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [1, 2]))
    assert sorted(results) == ["conflict", "saved"]
    assert store.read()[0] == 1


@pytest.mark.parametrize("token,code", [(None, 401), ("bad", 401), ("legacy", 403)])
def test_primary_only_read_and_write(client, token, code):
    client.cookies.clear()
    if token:
        client.cookies.set(auth.COOKIE_NAME, auth._serializer().dumps({"ok": True}) if token == "legacy" else token)
    assert client.get("/api/settings").status_code == code
    assert client.patch("/api/settings", json={"revision": 0, "changes": {"playback_cache_mb": 1}}).status_code == code
    assert store.read() == (0, {})


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.invalid"}, {"Origin": "null"},
    {"Origin": "http://testserver:9999"}, {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://evil.invalid", "X-Forwarded-Host": "evil.invalid"},
])
def test_cross_origin_writes_rejected(client, headers):
    response = client.patch("/api/settings", headers=headers,
                            json={"revision": 0, "changes": {"playback_cache_mb": 1}})
    assert response.status_code == 403 and store.read() == (0, {})


def test_form_posts_and_extra_envelope_fields_rejected(client):
    assert client.patch("/api/settings", content='{"revision":0,"changes":{"playback_cache_mb":1}}',
                        headers={"Content-Type": "text/plain", "Origin": "http://testserver"}).status_code == 415
    assert client.patch("/api/settings", json={"revision": True, "changes": {"playback_cache_mb": 1}}).status_code == 422
    assert client.patch("/api/settings", json={"revision": 0, "changes": {"playback_cache_mb": 1},
                                              "admin": True}).status_code == 422


def test_consumers_observe_override_without_restarting_services(monkeypatch, tmp_path):
    runtime_settings.update(runtime_settings.Update(revision=0, changes={"grid_hd_max_cameras": 3, "playback_cache_mb": 1}))
    request = Request({"type": "http", "app": FastAPI()})
    assert media.media_streams(request)["grid_hd_max_cameras"] == 3
    cache = tmp_path / "cache"
    cache.mkdir()
    derived = cache / "old.mp4"
    derived.write_bytes(b"x" * (1024 * 1024 + 1))
    original = tmp_path / "original.mp4"
    original.write_bytes(b"preserve")
    monkeypatch.setattr(playback, "_cache_root", lambda: cache)
    playback._evict()
    assert not derived.exists() and original.read_bytes() == b"preserve"


def test_unavailable_store_returns_sanitized_error(client, monkeypatch):
    def unavailable():
        raise ValueError("private database path/secret")
    monkeypatch.setattr(store, "read", unavailable)
    response = client.get("/api/settings")
    assert response.status_code == 503
    assert response.json() == {"detail": "Settings unavailable"}
