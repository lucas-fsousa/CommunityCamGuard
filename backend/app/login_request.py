"""Bound login body collection and concurrent work without retaining credentials."""

import asyncio
import threading

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from .origin_policy import require_browser_write

MAX_BODY_BYTES = 16 * 1024
BODY_TIMEOUT = 5.0
MAX_IN_FLIGHT = 8


def rejection(status: int, detail: str, *, retry: bool = False) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if retry:
        headers["Retry-After"] = "1"
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


class LoginRequests:
    def __init__(self):
        self._slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)

    async def handle(self, request: Request, handler):
        if not self._slots.acquire(blocking=False):
            return rejection(503, "Login temporarily busy", retry=True)
        try:
            return await self._handle(request, handler)
        finally:
            self._slots.release()

    async def _handle(self, request: Request, handler):
        require_browser_write(request)
        lengths = request.headers.getlist("content-length")
        if lengths:
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 10:
                return rejection(400, "Invalid login request")
            if int(lengths[0]) > MAX_BODY_BYTES:
                return rejection(413, "Login request too large")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return rejection(415, "Unsupported login encoding")
        content_type = request.headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
        if content_type != "application/json" and not (content_type.startswith("application/") and content_type.endswith("+json")):
            return rejection(415, "JSON login request required")
        body = bytearray()

        async def collect():
            async for chunk in request.stream():
                if len(chunk) > MAX_BODY_BYTES - len(body):
                    return False
                body.extend(chunk)
            return True

        try:
            accepted = await asyncio.wait_for(collect(), timeout=BODY_TIMEOUT)
        except TimeoutError:
            return rejection(408, "Login request timed out")
        except ClientDisconnect:
            return rejection(400, "Incomplete login request")
        if not accepted:
            return rejection(413, "Login request too large")
        if lengths and int(lengths[0]) != len(body):
            return rejection(400, "Invalid login request")

        async def receive():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        # Give FastAPI a bounded replay, rather than mutating Request private caches.
        bounded = Request(request.scope, receive=receive)
        try:
            return await handler(bounded)
        except RequestValidationError:
            # Validation errors can otherwise echo submitted credentials in `input`.
            return rejection(422, "Invalid login request")
