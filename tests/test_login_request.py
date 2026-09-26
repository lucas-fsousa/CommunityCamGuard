"""Synthetic ASGI bodies prove byte/deadline/concurrency boundaries without sockets."""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from backend.app import login_request
from backend.app.api.auth import router
from backend.app.login_request import MAX_BODY_BYTES, LoginRequests


def request(receive, headers=()):
    return Request({"type": "http", "method": "POST", "path": "/api/login",
                    "headers": list(headers), "query_string": b"", "scheme": "http"}, receive)


def run_body(chunks, headers=()):
    calls, delivered = [], []
    async def run():
        async def receive():
            calls.append(True)
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        async def handler(req):
            delivered.append(await req.body())
            return Response(status_code=204)
        return await LoginRequests().handle(request(receive, headers), handler)
    return asyncio.run(run()), calls, delivered


@pytest.mark.parametrize("headers,status", [
    ([(b"content-length", b"16385")], 413),
    ([(b"content-length", b"-1")], 400),
    ([(b"content-length", b"abc")], 400),
    ([(b"content-length", b"1"), (b"content-length", b"1")], 400),
    ([(b"content-length", b"9" * 100)], 400),
    ([(b"content-encoding", b"gzip")], 415),
    ([(b"content-type", b"multipart/form-data")], 415),
    ([(b"content-type", b"text/plain")], 415),
])
def test_header_rejections_do_not_read_or_parse_body(headers, status):
    response, reads, delivered = run_body([b"secret"], headers)
    assert response.status_code == status and not reads and not delivered
    assert response.headers["cache-control"] == "no-store"
    assert b"secret" not in response.body


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_actual_chunked_size_enforced_despite_missing_or_false_length(headers):
    response, reads, delivered = run_body([b"x" * MAX_BODY_BYTES, b"x", b"never-read"], headers)
    assert response.status_code == 413 and len(reads) == 2 and not delivered


def test_exact_limit_replayed_once_and_short_content_length_rejected():
    response, _, delivered = run_body([b"x" * 8192, b"y" * 8192], [(b"content-length", b"16384")])
    assert response.status_code == 204 and len(delivered[0]) == MAX_BODY_BYTES
    response, _, delivered = run_body([b"x"], [(b"content-length", b"2")])
    assert response.status_code == 400 and not delivered


def test_timeout_cancels_reader_and_releases_capacity(monkeypatch):
    monkeypatch.setattr(login_request, "BODY_TIMEOUT", 0.01)
    monkeypatch.setattr(login_request, "MAX_IN_FLIGHT", 1)
    async def run():
        limiter = LoginRequests()
        cancelled = []
        async def stalled():
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        async def handler(_):
            raise AssertionError("must not parse")
        response = await limiter.handle(request(stalled), handler)
        assert response.status_code == 408 and cancelled
        assert limiter._slots.acquire(blocking=False)
    asyncio.run(run())


def test_concurrency_rejection_does_not_read_and_parent_cancel_releases_slot(monkeypatch):
    monkeypatch.setattr(login_request, "MAX_IN_FLIGHT", 1)
    async def run():
        limiter = LoginRequests()
        started = asyncio.Event()
        async def stalled():
            started.set()
            await asyncio.Future()
        async def forbidden():
            raise AssertionError("busy request must not be read")
        async def handler(_):
            raise AssertionError("must not parse")
        first = asyncio.create_task(limiter.handle(request(stalled), handler))
        await started.wait()
        response = await limiter.handle(request(forbidden), handler)
        assert response.status_code == 503 and response.headers["retry-after"] == "1"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert limiter._slots.acquire(blocking=False)
    asyncio.run(run())


@pytest.mark.parametrize("disconnect", [False, True])
def test_handler_failure_and_disconnect_release_slot(monkeypatch, disconnect):
    monkeypatch.setattr(login_request, "MAX_IN_FLIGHT", 1)
    async def run():
        limiter = LoginRequests()
        async def receive():
            return {"type": "http.disconnect"} if disconnect else {"type": "http.request", "body": b"{}"}
        async def handler(_):
            raise RuntimeError("synthetic")
        if disconnect:
            assert (await limiter.handle(request(receive), handler)).status_code == 400
        else:
            with pytest.raises(RuntimeError, match="synthetic"):
                await limiter.handle(request(receive), handler)
        assert limiter._slots.acquire(blocking=False)
    asyncio.run(run())


@pytest.mark.parametrize("payload", [{"key": {"secret": "do-not-echo"}}, {"extra": "do-not-echo"}, ["do-not-echo"]])
def test_schema_errors_do_not_echo_credentials(payload):
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/login", json=payload)
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid login request"}
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers


def test_real_login_and_body_failure_do_not_affect_session_endpoints():
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/login", content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413
        response = client.post("/api/login", json={"key": "test-secret-key"})
        assert response.status_code == 200
        assert client.get("/api/me").json()["authenticated"]
        assert client.post("/api/logout").status_code == 200


@pytest.mark.parametrize("scheme,secure", [("http", False), ("https", True)])
def test_cookie_security_tracks_transport_not_forwarded_headers(scheme, secure):
    from http.cookies import SimpleCookie
    app = FastAPI(); app.include_router(router)
    with TestClient(app, base_url=f"{scheme}://testserver") as client:
        response = client.post("/api/login", json={"key": "test-secret-key"},
                               headers={"X-Forwarded-Proto": "http" if secure else "https"})
        assert response.status_code == 200
        cookie = SimpleCookie(response.headers["set-cookie"])["ccg_session"]
        assert bool(cookie["secure"]) == secure
        assert cookie["httponly"] and cookie["samesite"] == "lax" and cookie["path"] == "/"
        assert not cookie["domain"]
        response = client.post("/api/logout")
        cookie = SimpleCookie(response.headers["set-cookie"])["ccg_session"]
        assert cookie["max-age"] == "0" and bool(cookie["secure"]) == secure
        assert response.headers["cache-control"] == "no-store"
