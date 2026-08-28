"""WebSocket relay teardown tests; the upstream go2rtc socket is fully in-memory."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from starlette.websockets import WebSocketState

from backend.app.api import media as media_routes


class _Upstream:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.reader_cancelled = threading.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def send(self, value: str | bytes) -> None:
        self.sent.append(value)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.Future()  # upstream stays open until the browser side disconnects
        except asyncio.CancelledError:
            self.reader_cancelled.set()
            raise


class _Browser:
    def __init__(self) -> None:
        self.cookies = {media_routes.COOKIE_NAME: "test-token"}
        self.query_params = {"src": "cam_test_hd"}
        self.application_state = WebSocketState.CONNECTING
        self._messages = iter((
            {"type": "websocket.receive", "text": '{"type":"webrtc/offer","value":"test"}'},
            {"type": "websocket.disconnect"},
        ))

    async def accept(self) -> None:
        self.application_state = WebSocketState.CONNECTED

    async def receive(self) -> dict:
        return next(self._messages)

    async def close(self, **_kwargs) -> None:
        self.application_state = WebSocketState.DISCONNECTED

    async def send_bytes(self, _value: bytes) -> None:  # pragma: no cover - upstream stays idle
        raise AssertionError("unexpected upstream frame")

    async def send_text(self, _value: str) -> None:  # pragma: no cover - upstream stays idle
        raise AssertionError("unexpected upstream frame")


def test_ws_proxy_cancels_and_awaits_upstream_reader_on_browser_disconnect(monkeypatch):
    upstream = _Upstream()
    urls: list[str] = []

    def connect(url, **_kwargs):
        urls.append(url)
        return upstream

    monkeypatch.setattr(media_routes.websockets, "connect", connect)
    monkeypatch.setattr(media_routes, "verify_token", lambda _token: True)
    monkeypatch.setattr(
        media_routes,
        "get_settings",
        lambda: SimpleNamespace(go2rtc_api="http://go2rtc:1984"),
    )

    asyncio.run(media_routes.go2rtc_ws(_Browser()))

    assert urls and urls[0].endswith("/api/ws?src=cam_test_hd")
    assert upstream.sent == ['{"type":"webrtc/offer","value":"test"}']
    assert upstream.reader_cancelled.wait(timeout=1)
