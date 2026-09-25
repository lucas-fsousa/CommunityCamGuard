"""Revocable file delivery with synthetic bytes; no ffmpeg/cameras or live credentials."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import access_keys, auth, session_channels
from backend.app.api import recordings
from backend.app.config import get_settings
from backend.app.recording.delivery import SessionFileResponse


@pytest.fixture
def archive(monkeypatch):
    root = get_settings().recordings_dir
    root.mkdir(parents=True)
    source = root / "synthetic.mp4"
    source.write_bytes(bytes(range(256)) * 768)
    now = [datetime.now(UTC)]
    monkeypatch.setattr(access_keys, "_now", lambda: now[0])
    key = access_keys.create(access_keys.CreateKey(label="Guest", expires_at=now[0] + timedelta(hours=1)))
    token = auth.issue_temporary_token(key.secret)
    monkeypatch.setattr(session_channels, "CHECK_INTERVAL", 0.005)
    monkeypatch.setattr(recordings.playback, "cached_path", lambda _: None)
    monkeypatch.setattr(recordings.playback, "needs_transcode", lambda _: False)
    monkeypatch.setattr(recordings, "_recording_download_name", lambda *_: "Camera_UTC.mp4")
    return source, key, token, now


def scope(token, **extra):
    return {"type": "http", "method": "GET", "path": "/api/recordings/file", "http_version": "1.1",
            "headers": [(b"cookie", f"{auth.COOKIE_NAME}={token}".encode())], **extra}


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.parametrize("endpoint", ["file", "download"])
@pytest.mark.parametrize("range_header", [None, "bytes=10-99", "bytes=-20", "bytes=0-3,16-19"])
def test_real_http_temporary_ranges_and_no_store(archive, endpoint, range_header):
    source, _key, token, _now = archive
    app = FastAPI(); app.include_router(recordings.router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        response = client.get(f"/api/recordings/{endpoint}", params={"path": str(source)},
                              headers={"Range": range_header} if range_header else {})
    assert response.status_code == (206 if range_header else 200)
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["accept-ranges"] == "bytes"
    if range_header == "bytes=10-99":
        assert response.content == source.read_bytes()[10:100]
    elif range_header == "bytes=-20":
        assert response.content == source.read_bytes()[-20:]
    elif range_header:
        assert b"Content-Range: bytes 0-3/" in response.content
        assert b"Content-Range: bytes 16-19/" in response.content
    else:
        assert response.content == source.read_bytes()
    if endpoint == "download":
        assert "Camera_UTC.mp4" in response.headers["content-disposition"]


def test_expired_or_revoked_key_denied_before_file_delivery(archive):
    source, key, token, _now = archive
    access_keys.revoke(key.metadata.id)
    messages = []
    async def send(message):
        messages.append(message)
    asyncio.run(SessionFileResponse(source)(scope(token), receive, send))
    assert messages[0]["status"] == 401
    assert source.read_bytes()[:256] not in messages[-1]["body"]


@pytest.mark.parametrize("invalidation", ["revoke", "expire", "storage-error"])
def test_slow_inflight_transfer_is_cancelled_without_successful_completion(archive, monkeypatch, invalidation):
    source, key, token, now = archive
    messages, cancelled = [], []
    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body":
            if invalidation == "revoke":
                access_keys.revoke(key.metadata.id)
            elif invalidation == "expire":
                now[0] = key.metadata.expires_at
            else:
                def broken(*args):
                    raise sqlite3.OperationalError("private detail")
                monkeypatch.setattr(access_keys, "active_key", broken)
            try:
                await asyncio.Future()  # Simulate socket backpressure, not a fast buffered test client.
            finally:
                cancelled.append(True)
    async def run():
        with pytest.raises(ConnectionAbortedError, match="Session ended"):
            await asyncio.wait_for(SessionFileResponse(source)(scope(token), receive, send), 1)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(run())
    bodies = [m for m in messages if m["type"] == "http.response.body"]
    assert cancelled == [True] and len(bodies) == 1
    assert len(bodies[0]["body"]) == SessionFileResponse.chunk_size
    assert bodies[0]["more_body"]  # Never emit a false "successfully finished" tail.


def test_temporary_delivery_cannot_escape_via_pathsend(archive):
    source, _key, token, _now = archive
    messages = []
    request_scope = scope(token, extensions={"http.response.pathsend": {}})
    async def send(message):
        messages.append(message)
    asyncio.run(SessionFileResponse(source)(request_scope, receive, send))
    assert "http.response.pathsend" in request_scope["extensions"]  # No shared-scope mutation.
    assert all(m["type"] != "http.response.pathsend" for m in messages)
    assert b"".join(m.get("body", b"") for m in messages) == source.read_bytes()


def test_client_cancellation_reaps_delivery_and_guard(archive):
    source, _key, token, _now = archive
    cancelled = []
    async def run():
        started = asyncio.Event()
        async def send(message):
            if message["type"] == "http.response.body":
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.append(True)
        transfer = asyncio.create_task(SessionFileResponse(source)(scope(token), receive, send))
        await asyncio.wait_for(started.wait(), 1)
        transfer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await transfer
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(run())
    assert cancelled == [True]


def test_cached_copy_and_explicit_original_remain_distinct(archive, monkeypatch):
    source, _key, token, _now = archive
    derived = source.with_name("derived.mp4")
    derived.write_bytes(b"derived-payload")
    monkeypatch.setattr(recordings.playback, "cached_path", lambda _: derived)
    app = FastAPI(); app.include_router(recordings.router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        for original, expected in [(False, b"deri"), (True, bytes(range(4)))]:
            response = client.get("/api/recordings/file", params={"path": str(source), "original": original},
                                  headers={"Range": "bytes=0-3"})
            assert response.status_code == 206 and response.content == expected


def test_primary_keeps_pathsend_and_is_independent_of_key_store(archive, monkeypatch):
    source, _key, _token, _now = archive
    def forbidden(*args):
        raise AssertionError("Primary delivery must not poll temporary storage")
    monkeypatch.setattr(access_keys, "active_key", forbidden)
    messages = []
    async def send(message):
        messages.append(message)
    asyncio.run(SessionFileResponse(source)(scope(auth.issue_token(), extensions={"http.response.pathsend": {}}), receive, send))
    assert messages[-1]["type"] == "http.response.pathsend"


def test_range_failure_and_if_range_keep_standard_semantics(archive):
    source, _key, token, _now = archive
    app = FastAPI(); app.include_router(recordings.router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, token)
        url = "/api/recordings/file"
        invalid = client.get(url, params={"path": str(source)}, headers={"Range": "bytes=9999999-"})
        assert invalid.status_code == 416 and invalid.headers["cache-control"] == "private, no-store"
        initial = client.get(url, params={"path": str(source)})
        for etag, status in [(initial.headers["etag"], 206), ('"old"', 200)]:
            response = client.get(url, params={"path": str(source)}, headers={"Range": "bytes=0-3", "If-Range": etag})
            assert response.status_code == status


def test_prepare_allowed_but_missing_auth_and_traversal_still_denied(archive):
    source, _key, token, _now = archive
    app = FastAPI(); app.include_router(recordings.router)
    with TestClient(app) as client:
        assert client.get("/api/recordings/file", params={"path": str(source)}).status_code == 401
        client.cookies.set(auth.COOKIE_NAME, token)
        assert client.post("/api/recordings/prepare", params={"path": str(source)}).json()["ready"]
        assert client.get("/api/recordings/file", params={"path": str(source.parent.parent / "outside")}).status_code == 404
