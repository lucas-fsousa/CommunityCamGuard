"""Primary-session-only, allowlisted platform settings (not camera controls)."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from .. import runtime_settings
from ..auth import require_primary_session
from ..db.runtime_settings import RevisionConflict

router = APIRouter(prefix="/api/settings", tags=["settings"],
                   dependencies=[Depends(require_primary_session)])


def require_json_same_origin(request: Request) -> None:
    """Cookie-authenticated browser writes must not be cross-origin/form submissions."""
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/").lower()
    if ((origin is not None and origin.lower() != expected)
            or request.headers.get("sec-fetch-site", "").lower() == "cross-site"):
        raise HTTPException(403, "Cross-origin settings update denied")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON required")


@router.get("")
def read_settings(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return runtime_settings.snapshot()
    except (sqlite3.Error, ValueError, ValidationError):
        raise HTTPException(503, "Settings unavailable") from None


@router.patch("", dependencies=[Depends(require_json_same_origin)])
def update_settings(body: runtime_settings.Update, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return runtime_settings.update(body)
    except RevisionConflict:
        raise HTTPException(409, "Settings changed; reload before saving") from None
    except (sqlite3.Error, ValueError, ValidationError):
        raise HTTPException(503, "Settings unavailable") from None
