"""Dashboard authentication: a single secret key from ``.env`` gates access.

On login we compare the submitted key to ``dashboard_secret_key`` (constant-time) and, on
success, hand back a **signed, timed cookie** (itsdangerous). The cookie carries no secret —
it is safe even if exposed — so the key never travels again after login. Any key value /
length is accepted, per the product decision.
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_settings

COOKIE_NAME = "ccg_session"
MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_SALT = "ccg-session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().effective_signing_key, salt=_SALT)


def check_key(submitted: str) -> bool:
    """Constant-time comparison of the submitted key against the configured secret."""
    expected = get_settings().dashboard_secret_key
    return hmac.compare_digest((submitted or "").encode(), (expected or "").encode())


def issue_token() -> str:
    return _serializer().dumps({"ok": True})


def verify_token(token: str) -> bool:
    try:
        _serializer().loads(token, max_age=MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    return bool(token and verify_token(token))


def require_auth(request: Request) -> None:
    """FastAPI dependency: 401 unless the request carries a valid session cookie."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
