"""Dashboard session authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import COOKIE_NAME, MAX_AGE, check_key, issue_token, request_principal

router = APIRouter(prefix="/api", tags=["auth"])


class LoginIn(BaseModel):
    key: str


@router.post("/login")
def login(body: LoginIn, response: Response) -> dict:
    if not check_key(body.key):
        raise HTTPException(status_code=401, detail="Invalid key")
    response.set_cookie(
        COOKIE_NAME,
        issue_token(),
        httponly=True,
        samesite="lax",
        max_age=MAX_AGE,
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    principal = request_principal(request)
    return {
        "authenticated": principal is not None,
        "authentication": principal.authentication if principal else None,
        "can_manage": principal.can_manage if principal else False,
    }
