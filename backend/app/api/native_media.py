"""Authenticated delivery of an already-admitted shared source, never a startup API.

No default registry or driver factory: server-owned scheduling must explicitly
provide app.state.native_sources after resolving production ingress ownership.
"""

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket
from starlette.requests import HTTPConnection

from .. import auth
from ..camera_identity import valid_camera_id
from ..db import registry
from ..media.mp4_codec import h264_mime
from ..media.mp4_fanout import StreamClosedError, Subscription
from ..media.mse_protocol import mse_request
from ..media.source_registry import SourceRegistry, SourceUnavailable
from ..origin_policy import browser_origin_allowed
from ..session_channels import close_channel, run_guarded

router = APIRouter(prefix="/api/media/native", tags=["media"])
WRITE_TIMEOUT = 2.0
IDLE_TIMEOUT = 10.0
POLL_INTERVAL = 0.02


def claimed_sources(connection: HTTPConnection) -> tuple[str, ...]:
    state = getattr(getattr(connection, "app", None), "state", None)
    sources = getattr(state, "native_sources", None)
    return sources.camera_ids() if isinstance(sources, SourceRegistry) else ()


@router.websocket("/{camera_id}/ws")
async def native_video(socket: WebSocket, camera_id: str) -> None:
    await serve_native_video(socket, camera_id)


async def serve_native_video(socket: WebSocket, camera_id: str, *, source: str | None = None) -> None:
    """Serve the native endpoint or the existing player's exact source alias."""
    token = socket.cookies.get(auth.COOKIE_NAME) or ""
    expected_query = [] if source is None else [("src", source)]
    if (not valid_camera_id(camera_id) or not browser_origin_allowed(socket)
            or (source is not None and source not in {camera_id, camera_id + "_hd", camera_id + "_web"})
            or list(socket.query_params.multi_items()) != expected_query):
        await close_channel(socket, 1008)
        return

    def valid() -> bool:
        return (auth.verify_permission_token(token, "live")
                and registry.get_camera_by_id(camera_id) is not None)

    try:
        allowed = await asyncio.wait_for(asyncio.to_thread(valid), 5)
    except Exception:
        allowed = False
    if not allowed:
        await close_channel(socket, 1008)
        return
    sources = getattr(socket.app.state, "native_sources", None)
    if not isinstance(sources, SourceRegistry):
        await close_channel(socket, 1013)
        return
    await run_guarded(socket, token, lambda: _stream(socket, sources, camera_id), lambda _: valid())


async def _deliver(socket: WebSocket, reader: Subscription, max_level: int) -> None:
    init = reader.take()
    if init is None or not init.initialization:
        raise ValueError("MP4 initialization required")
    mime, level = h264_mime(init.data)
    if level > max_level:
        raise ValueError("client H264 level too low")
    await asyncio.wait_for(socket.send_json({"type": "mse", "value": mime}), WRITE_TIMEOUT)
    await asyncio.wait_for(socket.send_bytes(init.data), WRITE_TIMEOUT)
    del init
    deadline = time.monotonic() + IDLE_TIMEOUT
    while True:
        chunk = reader.take()
        if chunk is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("native output idle")
            await asyncio.sleep(POLL_INTERVAL)
            continue
        if chunk.initialization:
            raise ValueError("MP4 epoch changed")
        # One outstanding write; bytes remain shared with the fanout ring.
        await asyncio.wait_for(socket.send_bytes(chunk.data), WRITE_TIMEOUT)
        del chunk
        deadline = time.monotonic() + IDLE_TIMEOUT


async def _stream(socket: WebSocket, sources: SourceRegistry, camera_id: str) -> None:
    reader: Subscription | None = None
    tasks: list[asyncio.Task] = []
    code = 1000
    try:
        await asyncio.wait_for(socket.accept(), WRITE_TIMEOUT)
        message = await asyncio.wait_for(socket.receive(), 10)
        if message["type"] == "websocket.disconnect":
            return
        request = json.loads(mse_request(message.get("text")))
        levels = [int(codec[-2:], 16) for codec in request["value"].split(",")
                  if codec.startswith("avc1.")]
        if not levels:
            raise ValueError("H264 support required")
        reader = sources.subscribe(camera_id)

        async def disconnect() -> None:
            if (await socket.receive())["type"] != "websocket.disconnect":
                raise ValueError("MSE negotiation already completed")

        tasks = [asyncio.create_task(disconnect()),
                 asyncio.create_task(_deliver(socket, reader, max(levels)))]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except asyncio.CancelledError:
        code = 1008
        raise
    except ValueError:
        code = 1008
    except (SourceUnavailable, StreamClosedError):
        code = 1013
    except Exception:
        code = 1011
    finally:
        if reader is not None:
            reader.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_channel(socket, code)
