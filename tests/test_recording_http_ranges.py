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
