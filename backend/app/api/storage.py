"""Recording storage monitor endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_auth

router = APIRouter(prefix="/api", tags=["storage"])


@router.get("/storage", dependencies=[Depends(require_auth)])
def storage_status(request: Request) -> dict:
    monitor = getattr(request.app.state, "storage", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="storage monitor not running")
    return monitor.state().__dict__
