"""Primary media proxy failures must not log source URLs or upstream error text."""

import asyncio
import logging
from types import SimpleNamespace

from starlette.websockets import WebSocketState

from backend.app.api import media


def test_proxy_failure_log_omits_source_and_exception(monkeypatch, caplog):
    secret = "SYNTHETIC_PROXY_SECRET"

    class Socket:
        application_state = WebSocketState.CONNECTED

        def __init__(self):
            self.query_params = {"src": f"rtsp://user:{secret}@192.0.2.1/live"}

        async def accept(self):
            pass

        async def close(self):
            self.application_state = WebSocketState.DISCONNECTED

    def fail(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(media, "get_settings", lambda: SimpleNamespace(go2rtc_api="http://127.0.0.1:1984"))
    monkeypatch.setattr(media.websockets, "connect", fail)
    with caplog.at_level(logging.DEBUG, logger=media.__name__):
        asyncio.run(media._proxy_media(Socket()))
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert "rtsp://" not in caplog.text
