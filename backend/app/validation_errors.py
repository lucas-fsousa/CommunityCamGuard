"""Credential-safe HTTP schema errors, including routes added in the future."""

from fastapi import Request
from fastapi.responses import JSONResponse


async def invalid_request(_request: Request, _error: Exception) -> JSONResponse:
    # Do not serialize errors(), body, input, loc, ctx or custom validator messages:
    # each can contain submitted values, even when the final model uses SecretStr.
    return JSONResponse(
        {"detail": "Invalid request parameters"}, status_code=422,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
