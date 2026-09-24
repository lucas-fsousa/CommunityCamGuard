"""Primary-session-only, allowlisted platform settings (not camera controls)."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from .. import runtime_settings
from ..auth import require_primary_session
from ..db.runtime_settings import RevisionConflict
from .management_http import require_json_same_origin

router = APIRouter(prefix="/api/settings", tags=["settings"],
                   dependencies=[Depends(require_primary_session)])


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
