"""Bounded discovery credentials; no query secrets or reflected validation input."""

import asyncio
import json

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.requests import ClientDisconnect

from ..auth import require_auth

MAX_BYTES = 16 * 1024
BODY_TIMEOUT = 5.0


class ScanCredentials(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    username: str = Field(default="", max_length=1024)
    password: SecretStr = Field(default=SecretStr(""), max_length=1024)


def invalid(code: int, message: str) -> HTTPException:
    return HTTPException(code, message, headers={"Cache-Control": "no-store"})


async def read_scan_credentials(request: Request) -> ScanCredentials:
    # Authenticate before reading a potentially slow or sensitive body. Router-level
    # auth remains explicit for the route inventory and normal authority checks.
    require_auth(request)
    if request.query_params:
        raise invalid(400, "discovery query parameters are not accepted; use a JSON body")
    lengths = request.headers.getlist("content-length")
    if lengths:
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 10:
            raise invalid(400, "invalid discovery request")
        if int(lengths[0]) > MAX_BYTES:
            raise invalid(413, "discovery request too large")
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise invalid(415, "encoded discovery bodies are not accepted")
    media = request.headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise invalid(415, "JSON discovery body required")
    body = bytearray()
    async def collect():
        async for chunk in request.stream():
            if len(chunk) > MAX_BYTES - len(body):
                raise invalid(413, "discovery request too large")
            body.extend(chunk)
    try:
        await asyncio.wait_for(collect(), BODY_TIMEOUT)
    except TimeoutError:
        raise invalid(408, "discovery request timed out") from None
    except ClientDisconnect:
        raise invalid(400, "incomplete discovery request") from None
    if lengths and int(lengths[0]) != len(body):
        raise invalid(400, "invalid discovery request")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    try:
        data = json.loads(body, object_pairs_hook=unique) if body else {}
        return ScanCredentials.model_validate(data)
    except (ValueError, RecursionError):
        raise invalid(422, "invalid discovery credentials") from None
