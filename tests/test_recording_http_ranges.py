"""Exercise actual ASGI file responses (not just route return objects)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import recordings
from backend.app.auth import COOKIE_NAME, issue_token
from backend.app.config import get_settings


@pytest.fixture
def archive(monkeypatch):
    root = get_settings().recordings_dir
    root.mkdir(parents=True)
    source = root / "sample.mp4"
    source.write_bytes(bytes(range(256)) * 8)
    derived = root / "derived.mp4"
    derived.write_bytes(bytes(reversed(range(256))) * 12)
    monkeypatch.setattr(recordings.playback, "cached_path", lambda _: None)
    monkeypatch.setattr(recordings.playback, "needs_transcode", lambda _: False)
    app = FastAPI()
    app.include_router(recordings.router)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, issue_token())
        yield client, source, derived


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("range_header,start,end", [
    ("bytes=0-31", 0, 31), ("bytes=1024-1087", 1024, 1087),
    ("bytes=-16", -16, None), ("bytes=2000-", 2000, None),
])
def test_original_and_derived_support_random_access(archive, monkeypatch, cached, range_header, start, end):
    client, source, derived = archive
    if cached:
        monkeypatch.setattr(recordings.playback, "cached_path", lambda _: derived)
    data = (derived if cached else source).read_bytes()
    response = client.get("/api/recordings/file", params={"path": str(source)},
                          headers={"Range": range_header})
    assert response.status_code == 206
    assert response.headers["accept-ranges"] == "bytes"
    expected = data[start:] if end is None else data[start:end + 1]
    assert response.content == expected
    first = start if start >= 0 else len(data) + start
    last = len(data) - 1 if end is None else end
    assert response.headers["content-range"] == f"bytes {first}-{last}/{len(data)}"
    assert int(response.headers["content-length"]) == last - first + 1


def test_unsatisfiable_range_and_if_range_validation(archive):
    client, source, _ = archive
    params = {"path": str(source)}
    invalid = client.get("/api/recordings/file", params=params, headers={"Range": "bytes=9000-"})
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */2048"
    initial = client.get("/api/recordings/file", params=params)
    matched = client.get("/api/recordings/file", params=params,
                         headers={"Range": "bytes=2-5", "If-Range": initial.headers["etag"]})
    assert matched.status_code == 206 and len(matched.content) == 4
    stale = client.get("/api/recordings/file", params=params,
                       headers={"Range": "bytes=2-5", "If-Range": '"old"'})
    assert stale.status_code == 200 and stale.content == source.read_bytes()


def test_range_request_cannot_bypass_authentication(archive):
    client, source, _ = archive
    client.cookies.clear()
    response = client.get("/api/recordings/file", params={"path": str(source)},
                          headers={"Range": "bytes=0-31"})
    assert response.status_code == 401 and response.content != source.read_bytes()[:32]


def test_native_hevc_preparation_never_starts_encoder(archive, monkeypatch):
    client, source, _ = archive
    monkeypatch.setattr(recordings.playback, "video_codec", lambda _: "hevc")
    monkeypatch.setattr(recordings.playback, "prepare_transcode",
                        lambda _: pytest.fail("native playback started encoder"))
    response = client.post("/api/recordings/prepare",
                           params={"path": str(source), "native_hevc": True})
    assert response.status_code == 200
    assert response.json() == {"ready": True, "cached": False,
                               "transcoding": False, "original": True}


def test_hevc_default_still_prepares_compatible_copy(archive, monkeypatch):
    client, source, _ = archive
    started = []
    monkeypatch.setattr(recordings.playback, "needs_transcode", lambda _: True)
    monkeypatch.setattr(recordings.playback, "transcode_in_progress", lambda _: bool(started))
    monkeypatch.setattr(recordings.playback, "prepare_transcode", lambda target: started.append(target))
    response = client.post("/api/recordings/prepare", params={"path": str(source)})
    assert response.status_code == 200 and response.json()["transcoding"]
    assert started == [source] and "original" not in response.json()


def test_get_never_starts_or_restarts_conversion_and_post_owns_preparation(archive, monkeypatch):
    client, source, derived = archive
    state = {"started": 0, "cached": False, "running": False}
    monkeypatch.setattr(recordings.playback, "needs_transcode", lambda _: True)
    monkeypatch.setattr(recordings.playback, "cached_path", lambda _: derived if state["cached"] else None)
    monkeypatch.setattr(recordings.playback, "transcode_in_progress", lambda _: state["running"])
    def prepare(_):
        state["started"] += 1
        state["running"] = True
    monkeypatch.setattr(recordings.playback, "prepare_transcode", prepare)
    params = {"path": str(source)}
    for _ in range(3):
        response = client.get("/api/recordings/file", params=params, headers={"Range": "bytes=0-15"})
        assert response.status_code == 409
        assert "POST /api/recordings/prepare" in response.json()["detail"]
        assert response.headers["cache-control"] == "no-store"
        assert not client.get("/api/recordings/playback-status", params=params).json()["transcoding"]
    assert state["started"] == 0
    denied = client.post("/api/recordings/prepare", params=params, headers={"Origin": "https://other.invalid"})
    assert denied.status_code == 403 and state["started"] == 0
    assert client.post("/api/recordings/prepare", params=params).json()["transcoding"]
    assert client.get("/api/recordings/file", params=params).status_code == 409
    assert client.post("/api/recordings/prepare", params=params).json()["transcoding"]
    assert state["started"] == 1
    state["cached"] = True
    state["running"] = False
    response = client.get("/api/recordings/file", params=params, headers={"Range": "bytes=0-15"})
    assert response.status_code == 206 and response.content == derived.read_bytes()[:16]
    state["cached"] = False  # Eviction cannot silently restart a job via GET.
    assert client.get("/api/recordings/file", params=params).status_code == 409
    assert state["started"] == 1
    assert not client.get("/api/recordings/playback-status", params=params).json()["transcoding"]
    assert client.post("/api/recordings/prepare", params=params).json()["transcoding"]
    assert state["started"] == 2


def test_busy_encoder_only_affects_explicit_post(archive, monkeypatch):
    client, source, _ = archive
    monkeypatch.setattr(recordings.playback, "needs_transcode", lambda _: True)
    monkeypatch.setattr(recordings.playback, "transcode_in_progress", lambda _: False)
    def busy(_):
        raise recordings.PlaybackBusy("synthetic busy")
    monkeypatch.setattr(recordings.playback, "prepare_transcode", busy)
    params = {"path": str(source)}
    assert client.get("/api/recordings/file", params=params).status_code == 409
    response = client.post("/api/recordings/prepare", params=params)
    assert response.status_code == 429 and response.headers["retry-after"] == "5"


@pytest.mark.parametrize("codec", ["h264", ""])
def test_native_hint_does_not_select_unknown_or_other_codecs(archive, monkeypatch, codec):
    client, source, _ = archive
    monkeypatch.setattr(recordings.playback, "video_codec", lambda _: codec)
    response = client.post("/api/recordings/prepare", params={"path": str(source), "native_hevc": True})
    assert response.status_code == 200 and "original" not in response.json()


def test_original_delivery_ignores_cache_and_preserves_range(archive, monkeypatch):
    client, source, derived = archive
    monkeypatch.setattr(recordings.playback, "cached_path", lambda _: derived)
    monkeypatch.setattr(recordings.playback, "needs_transcode",
                        lambda _: pytest.fail("original request probed/transcoded"))
    params = {"path": str(source), "original": True}
    response = client.get("/api/recordings/file", params=params, headers={"Range": "bytes=2-9"})
    assert response.status_code == 206 and response.content == source.read_bytes()[2:10]
    client.cookies.clear()
    assert client.get("/api/recordings/file", params=params).status_code == 401
    assert client.post("/api/recordings/prepare", params={"path": str(source),
                       "native_hevc": True}).status_code == 401


@pytest.mark.parametrize("endpoint,method,preference", [
    ("file", "get", "original"), ("prepare", "post", "native_hevc"),
])
def test_native_preferences_do_not_bypass_archive_root(archive, tmp_path, endpoint, method, preference):
    client, _, _ = archive
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private")
    response = getattr(client, method)(f"/api/recordings/{endpoint}",
                                       params={"path": str(outside), preference: True})
    assert response.status_code == 404
