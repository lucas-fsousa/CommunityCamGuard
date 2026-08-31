"""Vendor-neutral bounded server-to-camera audio-message endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, WebSocket
from starlette.websockets import WebSocketState

from ..audio_diagnostics import PcmLevelAccumulator
from ..auth import COOKIE_NAME, require_auth, verify_token
from ..drivers import ControlNotReady, ControlOperationError, Unsupported
from ..services import CameraNotFound, ControlBusy, send_audio_message, send_audio_stream
from .local_only import require_local_request, require_local_websocket

MAX_PCM_BYTES = 160_000  # Ten seconds of mono 8 kHz signed 16-bit PCM.
PCM_FRAME_BYTES = 320  # One codec-neutral 20 ms block of mono 8 kHz s16le input.
STREAM_QUEUE_FRAMES = 25  # At most 500 ms of camera-bound PCM backlog.
STREAM_IDLE_SECONDS = 2.0
_CAMERA_ID = re.compile(r"^cam_[0-9a-f]{24}$")
_STREAM_STOP = object()
log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/cameras/{camera_id}/intercom",
    dependencies=[Depends(require_auth), Depends(require_local_request)],
    tags=["cameras"],
)
stream_router = APIRouter(prefix="/api/cameras/{camera_id}/intercom", tags=["cameras"])


def _failure(exc: Exception) -> HTTPException:
    if isinstance(exc, CameraNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, Unsupported):
        return HTTPException(status_code=501, detail="this camera doesn't support audio messages")
    if isinstance(exc, (ControlNotReady, ControlBusy)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


async def _bounded_pcm(request: Request) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "audio/pcm":
        raise HTTPException(
            status_code=415,
            detail="content type must be audio/pcm (8 kHz, mono, signed 16-bit little-endian)",
        )
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content length") from exc
        if declared_size > MAX_PCM_BYTES:
            raise HTTPException(status_code=413, detail="audio message exceeds ten seconds")

    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_PCM_BYTES:
            raise HTTPException(status_code=413, detail="audio message exceeds ten seconds")
        payload.extend(chunk)
    if len(payload) < PCM_FRAME_BYTES or len(payload) % PCM_FRAME_BYTES:
        raise HTTPException(
            status_code=422,
            detail="audio must contain complete 20 ms PCM frames",
        )
    return bytes(payload)


@router.post("/messages")
async def create_audio_message(
    request: Request,
    response: Response,
    camera_id: str = Path(pattern=r"^cam_[0-9a-f]{24}$"),
) -> dict[str, object]:
    """Play up to ten seconds of fixed-format PCM through the selected camera driver."""

    pcm16le = await _bounded_pcm(request)
    levels = PcmLevelAccumulator()
    levels.feed(pcm16le)
    try:
        result = await asyncio.to_thread(send_audio_message, camera_id, pcm16le)
    except (
        CameraNotFound,
        Unsupported,
        ControlNotReady,
        ControlBusy,
        ControlOperationError,
    ) as exc:
        raise _failure(exc) from exc
    if not result.completed:
        raise HTTPException(status_code=502, detail="camera did not complete the audio message")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    log.warning(
        "intercom_audio %s",
        json.dumps(
            {"mode": "message", "camera_id": camera_id, **levels.public(), **result.public()},
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    return {"id": camera_id, **result.public()}


def _pcm_queue_iterator(
    chunks: queue.Queue[bytes | object],
    stop: threading.Event,
    ready: threading.Event,
):
    """Bridge a bounded queue to the synchronous driver without blocking the event loop."""

    ready.set()
    idle_deadline = time.monotonic() + STREAM_IDLE_SECONDS
    while not stop.is_set():
        try:
            item = chunks.get(timeout=min(0.25, max(0.01, idle_deadline - time.monotonic())))
        except queue.Empty:
            if time.monotonic() >= idle_deadline:
                raise TimeoutError("audio stream became idle") from None
            continue
        if item is _STREAM_STOP:
            return
        if not isinstance(item, bytes):
            raise ValueError("invalid audio stream queue item")
        idle_deadline = time.monotonic() + STREAM_IDLE_SECONDS
        yield item


async def _send_ws_json(websocket: WebSocket, payload: dict[str, object]) -> None:
    if websocket.application_state == WebSocketState.CONNECTED:
        await websocket.send_text(json.dumps(payload, separators=(",", ":")))


@stream_router.websocket("/stream")
async def stream_audio(websocket: WebSocket, camera_id: str) -> None:
    """Feed one bounded PCM push-to-talk session through a driver worker."""

    if not _CAMERA_ID.fullmatch(camera_id) or not verify_token(
        websocket.cookies.get(COOKIE_NAME) or ""
    ):
        await websocket.close(code=1008)
        return
    try:
        require_local_websocket(websocket)
    except HTTPException:
        await websocket.close(code=1008)
        return

    chunks: queue.Queue[bytes | object] = queue.Queue(maxsize=STREAM_QUEUE_FRAMES)
    stop = threading.Event()
    ready = threading.Event()
    iterator = _pcm_queue_iterator(chunks, stop, ready)
    await websocket.accept()
    worker = asyncio.create_task(asyncio.to_thread(send_audio_stream, camera_id, iterator))
    total_bytes = 0
    levels = PcmLevelAccumulator()
    reported_error = False
    graceful_stop = False
    try:
        for _attempt in range(
            240
        ):  # Route setup may take time, but never more than 12 seconds here.
            if ready.is_set() or worker.done():
                break
            await asyncio.sleep(0.05)
        if not ready.is_set():
            if worker.done():
                await worker
            raise TimeoutError("camera audio stream did not become ready")
        await _send_ws_json(websocket, {"type": "ready", "max_ms": 10_000})

        while total_bytes < MAX_PCM_BYTES and not worker.done():
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
            except TimeoutError:
                continue
            if message["type"] == "websocket.disconnect":
                break
            payload = message.get("bytes")
            if payload is not None:
                if len(payload) != PCM_FRAME_BYTES:
                    await _send_ws_json(
                        websocket,
                        {"type": "error", "detail": "one 20 ms PCM frame is required"},
                    )
                    reported_error = True
                    break
                if total_bytes + len(payload) > MAX_PCM_BYTES:
                    await _send_ws_json(
                        websocket,
                        {"type": "error", "detail": "audio stream exceeds ten seconds"},
                    )
                    reported_error = True
                    break
                try:
                    chunks.put_nowait(bytes(payload))
                except queue.Full:
                    await _send_ws_json(
                        websocket,
                        {"type": "error", "detail": "camera audio stream is congested"},
                    )
                    reported_error = True
                    break
                levels.feed(payload)
                total_bytes += len(payload)
                if total_bytes == MAX_PCM_BYTES:
                    graceful_stop = True
                continue
            if (message.get("text") or "").strip().lower() == "stop":
                graceful_stop = True
                break
            await _send_ws_json(
                websocket,
                {"type": "error", "detail": "binary PCM or stop was expected"},
            )
            reported_error = True
            break
    except (
        CameraNotFound,
        Unsupported,
        ControlNotReady,
        ControlBusy,
        ControlOperationError,
    ) as exc:
        await _send_ws_json(websocket, {"type": "error", "detail": str(_failure(exc).detail)})
        reported_error = True
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        await _send_ws_json(websocket, {"type": "error", "detail": str(exc)})
        reported_error = True
    finally:
        if graceful_stop:
            try:
                await asyncio.to_thread(chunks.put, _STREAM_STOP, True, 1.0)
            except queue.Full:
                stop.set()
        else:
            stop.set()
            try:
                chunks.put_nowait(_STREAM_STOP)
            except queue.Full:
                pass
        try:
            result = await asyncio.wait_for(asyncio.shield(worker), timeout=12.0)
            if total_bytes:
                log.warning(
                    "intercom_audio %s",
                    json.dumps(
                        {
                            "mode": "stream",
                            "camera_id": camera_id,
                            **levels.public(),
                            **result.public(),
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                if result.completed:
                    await _send_ws_json(websocket, {"type": "complete", **result.public()})
                elif not reported_error:
                    await _send_ws_json(
                        websocket,
                        {"type": "error", "detail": "camera did not complete the audio stream"},
                    )
        except Exception:
            if not reported_error:
                await _send_ws_json(
                    websocket,
                    {"type": "error", "detail": "camera audio stream failed"},
                )
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass
