"""Open-channel authorization lifecycle; only fake sockets, media and audio workers."""

import asyncio
import threading

import pytest
from starlette.websockets import WebSocketState

from backend.app import auth, session_channels
from backend.app.api import intercom, media
from backend.app.camera_identity import stable_camera_id
from backend.app.drivers.contracts import AudioMessageResult


class Socket:
    def __init__(self):
        self.application_state = WebSocketState.CONNECTING
        self.cookies = {auth.COOKIE_NAME: auth.issue_token()}
        self.query_params = {"src": "synthetic"}
        self.closed = []
        self.accepted = False

    async def accept(self):
        self.accepted = True
        self.application_state = WebSocketState.CONNECTED

    async def close(self, code=1000):
        self.closed.append(code)
        self.application_state = WebSocketState.DISCONNECTED

    async def receive(self):
        await asyncio.Future()

    async def send_text(self, value):
        pass


@pytest.fixture(autouse=True)
def fast_checks(monkeypatch):
    monkeypatch.setattr(session_channels, "CHECK_INTERVAL", 0.005)


@pytest.mark.parametrize("failure", ["invalid", "error", "timeout"])
def test_guard_reaps_operation_and_closes_fail_closed(monkeypatch, failure):
    socket = Socket()
    cleaned = []
    cancelled = []
    release = threading.Event()
    def verify(_token):
        if failure == "error":
            raise RuntimeError("do not log credential/store failure")
        if failure == "timeout":
            release.wait(0.1)
        return failure == "timeout"
    monkeypatch.setattr(session_channels, "CHECK_TIMEOUT", 0.01)
    async def operation():
        await socket.accept()
        try:
            await asyncio.Future()
        finally:
            cleaned.append(True)
    async def run():
        try:
            await asyncio.wait_for(session_channels.run_guarded(
                socket, "opaque", operation, verify, on_cancel=lambda: cancelled.append(True)), 1)
        finally:
            release.set()
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(run())
    assert socket.closed == [1008] and cleaned == [True]
    assert cancelled


def test_normal_completion_does_not_close_as_unauthorized():
    socket = Socket()
    async def operation():
        return
    asyncio.run(session_channels.run_guarded(socket, "opaque", operation, lambda _: True))
    assert socket.closed == []


def test_operation_failure_propagates_without_leaking_guard():
    async def operation():
        raise ValueError("operation failed")
    async def run():
        with pytest.raises(ValueError, match="operation failed"):
            await session_channels.run_guarded(Socket(), "opaque", operation, lambda _: True)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(run())


def test_parent_cancellation_reaps_guard_and_operation():
    cleaned = []
    async def run():
        started = asyncio.Event()
        async def operation():
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.append(True)
        task = asyncio.create_task(session_channels.run_guarded(Socket(), "opaque", operation, lambda _: True))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(run())
    assert cleaned == [True]


def test_media_expired_session_closes_upstream_and_both_pumps(monkeypatch):
    socket = Socket()
    cleaned = []
    valid = [True]
    class Upstream:
        async def __aenter__(self):
            valid[0] = False
            return self
        async def __aexit__(self, *args):
            cleaned.append("upstream")
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                await asyncio.Future()
            finally:
                cleaned.append("reader")
    monkeypatch.setattr(media, "verify_token", lambda _: valid[0])
    monkeypatch.setattr(media.websockets, "connect", lambda *args, **kwargs: Upstream())
    async def run():
        await asyncio.wait_for(media.go2rtc_ws(socket), 1)
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(run())
    assert socket.accepted and socket.closed[0] == 1008
    assert sorted(cleaned) == ["reader", "upstream"]


def test_media_rechecks_real_cookie_expiry(monkeypatch):
    from itsdangerous import TimestampSigner
    now = [1000000]
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda _: now[0])
    socket = Socket()
    async def operation(_socket):
        await socket.accept()
        now[0] += auth.MAX_AGE + 1
        await asyncio.Future()
    monkeypatch.setattr(media, "_proxy_media", operation)
    asyncio.run(asyncio.wait_for(media.go2rtc_ws(socket), 1))
    assert socket.closed == [1008]


def test_media_unauthorized_never_contacts_upstream(monkeypatch):
    socket = Socket()
    socket.cookies = {}
    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized upstream access")
    monkeypatch.setattr(media.websockets, "connect", forbidden)
    asyncio.run(media.go2rtc_ws(socket))
    assert not socket.accepted and socket.closed == [1008]


def test_intercom_invalidated_session_stops_idle_driver(monkeypatch):
    socket = Socket()
    stopped = threading.Event()
    valid = [True]
    def driver(_id, chunks):
        valid[0] = False
        try:
            for _ in chunks:
                raise AssertionError("test must not send real/fake audio frames")
        finally:
            stopped.set()
        return AudioMessageResult(0, 0, 0, 0, True, True, True)
    monkeypatch.setattr(intercom, "verify_token", lambda _: valid[0])
    monkeypatch.setattr(intercom, "require_local_websocket", lambda _: None)
    monkeypatch.setattr(intercom, "send_audio_stream", driver)
    camera_id = stable_camera_id("mac", "aa:bb:cc:dd:ee:03")
    asyncio.run(asyncio.wait_for(intercom.stream_audio(socket, camera_id), 1))
    assert socket.closed[0] == 1008 and stopped.is_set()
