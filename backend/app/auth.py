"""Dashboard authentication: a single secret key from ``.env`` gates access.

On login we compare the submitted key to ``dashboard_secret_key`` (constant-time) and, on
success, hand back a **signed, timed cookie** (itsdangerous). The cookie does not contain
the login key, but is itself a bearer credential: anyone holding it can reuse the session.
Never expose/log it. The login key need not travel again after login. Any key value /
length is accepted, per the product decision.
"""
from __future__ import annotations

import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request, status
from itsdangerous import BadData, URLSafeTimedSerializer

from .config import get_settings

COOKIE_NAME = "ccg_session"
MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_SALT = "ccg-session"
_SESSION_ID = re.compile(r"[0-9a-f]{32}")
_MAX_TOKEN_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    """Verified authentication origin, not client-supplied administrative claims."""

    authentication: Literal["primary", "legacy"]
    session_id: str | None = None

    @property
    def can_manage(self) -> bool:
        return self.authentication == "primary"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().effective_signing_key, salt=_SALT)


def check_key(submitted: str) -> bool:
    """Constant-time comparison of the submitted key against the configured secret."""
    expected = get_settings().dashboard_secret_key
    return hmac.compare_digest((submitted or "").encode(), (expected or "").encode())


def issue_token() -> str:
    """Issue only after primary-key verification; never accept a caller-selected role."""
    return _serializer().dumps({"v": 1, "authentication": "primary", "sid": secrets.token_hex(16)})


def token_principal(token: str) -> SessionPrincipal | None:
    """Validate signature/age and an exact supported payload; unknown kinds fail closed.

    Old canonical cookies retain ordinary access until their original expiry, but
    never gain management permission. A fresh primary-key login is required for that.
    Session IDs are correlation identities, not a revocation database.
    """
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        payload = _serializer().loads(token, max_age=MAX_AGE)
    except BadData:
        return None
    if not isinstance(payload, dict):
        return None
    if set(payload) == {"ok"} and payload["ok"] is True:
        return SessionPrincipal("legacy")
    if set(payload) != {"v", "authentication", "sid"}:
        return None
    if type(payload["v"]) is not int or payload["v"] != 1:
        return None
    if payload["authentication"] != "primary":
        return None
    sid = payload["sid"]
    if not isinstance(sid, str) or not _SESSION_ID.fullmatch(sid):
        return None
    return SessionPrincipal("primary", sid)


def verify_token(token: str) -> bool:
    """Shared HTTP/WebSocket authentication compatibility entry point."""
    return token_principal(token) is not None


def request_principal(request: Request) -> SessionPrincipal | None:
    return token_principal(request.cookies.get(COOKIE_NAME) or "")


def is_authenticated(request: Request) -> bool:
    return request_principal(request) is not None


def require_auth(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries a valid session cookie."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def require_primary_session(request: Request) -> SessionPrincipal:
    """Management dependency: authenticate first, then require a primary-key origin."""
    principal = request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not principal.can_manage:
        raise HTTPException(status_code=403, detail="Sign in with the primary key to manage settings")
    return principal
