from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from backend.app.api import intercom
from backend.app.api.local_only import require_local_request
from backend.app.camera_identity import stable_camera_id
from backend.app.drivers.contracts import AudioMessageResult

CAMERA_ID = stable_camera_id("mac", "aa:bb:cc:dd:ee:03")


def _request(payload: bytes, content_type: str = "audio/pcm", *, declared: int | None = None):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    headers = [(b"content-type", content_type.encode())]
    if declared is not None:
        headers.append((b"content-length", str(declared).encode()))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)


def test_audio_message_dispatches_only_bounded_pcm_off_event_loop(monkeypatch) -> None:
    pcm = bytes(640)
    observed = []

    def dispatch(camera_id, payload):
        observed.append((camera_id, payload))
        return AudioMessageResult(40, 2, 2, 2, True, True, True)

    async def to_thread(function, *args):
        observed.append("thread")
        return function(*args)

    monkeypatch.setattr(intercom, "send_audio_message", dispatch)
    monkeypatch.setattr(intercom.asyncio, "to_thread", to_thread)
    response = Response()
    result = asyncio.run(intercom.create_audio_message(_request(pcm), response, CAMERA_ID))

    assert observed == ["thread", (CAMERA_ID, pcm)]
    assert result == {
        "id": CAMERA_ID,
        "duration_ms": 40,
        "requested_frames": 2,
        "sent_frames": 2,
        "acknowledged_frames": 2,
        "direct_connection": True,
        "session_completed": True,
        "route_released": True,
        "completed": True,
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("request_obj", "status"),
    [
        (_request(bytes(320), "application/octet-stream"), 415),
        (_request(bytes(318)), 422),
        (_request(bytes(320), declared=intercom.MAX_PCM_BYTES + 1), 413),
        (_request(bytes(intercom.MAX_PCM_BYTES + 320)), 413),
    ],
)
def test_audio_message_rejects_ambiguous_or_unbounded_payloads(request_obj, status) -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(intercom.create_audio_message(request_obj, Response(), CAMERA_ID))
    assert caught.value.status_code == status


def test_audio_message_routes_are_authenticated_and_lan_only() -> None:
    for route in intercom.router.routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert intercom.require_auth in dependencies
        assert require_local_request in dependencies


def test_incomplete_camera_delivery_is_an_http_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        intercom,
        "send_audio_message",
        lambda *_args: AudioMessageResult(20, 1, 1, 0, True, True, True),
    )
    with pytest.raises(HTTPException) as caught:
        asyncio.run(intercom.create_audio_message(_request(bytes(320)), Response(), CAMERA_ID))
    assert caught.value.status_code == 502
