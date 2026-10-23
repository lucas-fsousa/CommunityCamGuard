"""Restricted MSE-only media bridge for staged temporary sessions.

Never forward arbitrary source URLs, binary uploads or WebRTC/HLS negotiation.
The normal primary/legacy proxy remains separate and unchanged.
"""

import asyncio
import re
from urllib.parse import quote

import websockets
from starlette.websockets import WebSocket, WebSocketState

from .. import auth
from ..config import get_settings
from ..db import registry
from ..media.mse_protocol import mse_request
from ..origin_policy import browser_origin_allowed
from ..session_channels import close_channel, run_guarded

_SOURCE = re.compile(r"(cam_[0-9a-f]{24})_(hd|web)")


async def serve_temporary_media(socket: WebSocket, token: str) -> None:
    src = socket.query_params.get("src", "")
    match = _SOURCE.fullmatch(src)
    if (not match or list(socket.query_params.multi_items()) != [("src", src)]
            or not browser_origin_allowed(socket)):
        await socket.close(code=1008)
        return

    camera_id = match[1]
    def valid() -> bool:
        return auth.verify_permission_token(token, "live") and registry.get_camera_by_id(camera_id) is not None

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
            await close_channel(socket, 1008)
    except Exception:
        if socket.application_state != WebSocketState.DISCONNECTED:
            await close_channel(socket, 1011)
    finally:
        if socket.application_state != WebSocketState.DISCONNECTED:
            await close_channel(socket)
