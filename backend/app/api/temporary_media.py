"""Restricted MSE-only media bridge for staged temporary sessions.

Never forward arbitrary source URLs, binary uploads or WebRTC/HLS negotiation.
The normal primary/legacy proxy remains separate and unchanged.
"""

import asyncio
import json
import re
from urllib.parse import quote

import websockets
from starlette.websockets import WebSocket, WebSocketState

from .. import auth
from ..config import get_settings
from ..db import registry
from ..session_channels import run_guarded

_SOURCE = re.compile(r"(cam_[0-9a-f]{24})_(hd|web)")
_CODECS = frozenset({"avc1.640029", "avc1.64002A", "avc1.640033", "hvc1.1.6.L153.B0",
                     "mp4a.40.2", "mp4a.40.5", "flac", "opus"})


def mse_request(raw: object) -> str:
    """Validate the exact bounded request emitted by the bundled VideoRTC client."""
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate field")
            obj[key] = value
        return obj
    if not isinstance(raw, str) or len(raw) > 512:
        raise ValueError("Invalid MSE request")
    message = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(message, dict) or set(message) != {"type", "value"} or message["type"] != "mse":
        raise ValueError("MSE only")
    value = message["value"]
    if not isinstance(value, str):
        raise ValueError("Invalid codecs")
    codecs = value.split(",")
    if not codecs or len(codecs) != len(set(codecs)) or not set(codecs) <= _CODECS:
        raise ValueError("Invalid codecs")
    return json.dumps(message, separators=(",", ":"))


async def serve_temporary_media(socket: WebSocket, token: str) -> None:
    src = socket.query_params.get("src", "")
    match = _SOURCE.fullmatch(src)
    expected_origin = ("https" if socket.url.scheme == "wss" else "http") + "://" + socket.url.netloc
    origin = socket.headers.get("origin")
    if (not match or list(socket.query_params.multi_items()) != [("src", src)]
            or (origin is not None and origin.lower() != expected_origin.lower())
            or socket.headers.get("sec-fetch-site", "").lower() == "cross-site"):
        await socket.close(code=1008)
        return

    camera_id = match[1]
    def valid() -> bool:
        return auth.verify_token(token) and registry.get_camera_by_id(camera_id) is not None

    try:
        allowed = await asyncio.wait_for(asyncio.to_thread(valid), 5)
    except Exception:
        allowed = False
    if not allowed:
        await socket.close(code=1008)
        return
    await run_guarded(socket, token, lambda: _bridge(socket, src), lambda _: valid())


async def _bridge(socket: WebSocket, src: str) -> None:
    await socket.accept()
    try:
        first = await asyncio.wait_for(socket.receive(), 10)
        if first["type"] == "websocket.disconnect":
            return
        request = mse_request(first.get("text"))
        api = get_settings().go2rtc_api.rstrip("/")
        url = "ws" + api[4:] + "/api/ws?src=" + quote(src, safe="")
        async with websockets.connect(url, open_timeout=5, max_size=4 * 1024 * 1024, max_queue=4) as upstream:
            await upstream.send(request)

            async def reject_more_requests():
                message = await socket.receive()
                if message["type"] != "websocket.disconnect":
                    await socket.close(code=1008)

            async def deliver():
                async for frame in upstream:
                    if isinstance(frame, bytes):
                        await socket.send_bytes(frame)
                    else:
                        await socket.send_text(frame)

            tasks = [asyncio.create_task(reject_more_requests()), asyncio.create_task(deliver())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except (ValueError, TimeoutError):
        if socket.application_state != WebSocketState.DISCONNECTED:
            await socket.close(code=1008)
    except Exception:
        if socket.application_state != WebSocketState.DISCONNECTED:
            await socket.close(code=1011)
    finally:
        if socket.application_state != WebSocketState.DISCONNECTED:
            await socket.close()
