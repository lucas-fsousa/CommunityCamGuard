"""Temporary-key lifecycle foundation, deliberately not wired into login yet.

Keys have a public random ID plus a 256-bit random secret. SHA-256 is a verifier
for generated high-entropy credentials, NOT a password-hashing substitute.
Storage errors propagate: HTTP/auth boundaries must fail closed, not grant
access on DB failure. Session/channel invalidation must land before activation.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .db import access_keys as repository

_KEY_ID = re.compile(r"[0-9a-f]{32}")
_CREDENTIAL = re.compile(r"ccg_tmp_([0-9a-f]{32})\.([A-Za-z0-9_-]{43})")


class CreateKey(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80, strict=True)
    expires_at: AwareDatetime

    @field_validator("expires_at", mode="before")
    @classmethod
    def explicit_datetime(cls, value):
        if not isinstance(value, str | datetime):
            raise ValueError("An explicit timezone-aware date is required")
        return datetime.fromisoformat(value) if isinstance(value, str) else value


class KeyMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    label: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    status: Literal["active", "expired", "revoked"]


@dataclass(frozen=True, slots=True)
class IssuedKey:
    metadata: KeyMetadata
    # Avoid leaking the one-time credential through object repr/debug logging.
    secret: str = field(repr=False)


class InvalidExpiration(ValueError):
    """User input cannot represent a future UTC expiry."""


def _now() -> datetime:
    return datetime.now(UTC)


def _metadata(row: dict, now: datetime) -> KeyMetadata:
    revoked = row["revoked_at"]
    return KeyMetadata(
        id=row["id"], label=row["label"],
        created_at=datetime.fromtimestamp(row["created_at"], UTC),
        expires_at=datetime.fromtimestamp(row["expires_at"], UTC),
        revoked_at=datetime.fromtimestamp(revoked, UTC) if revoked is not None else None,
        status="revoked" if revoked is not None else "expired" if row["expires_at"] <= now.timestamp() else "active",
    )


def create(body: CreateKey) -> IssuedKey:
    now = _now()
    try:
        expiry = body.expires_at.astimezone(UTC)
    except (OverflowError, ValueError):
        raise InvalidExpiration("Expiration must be a valid UTC date") from None
    if expiry <= now:
        raise InvalidExpiration("Expiration must be in the future")
    key_id = secrets.token_hex(16)
    secret = f"ccg_tmp_{key_id}.{secrets.token_urlsafe(32)}"
    verifier = hashlib.sha256(secret.encode("ascii")).hexdigest()
    repository.insert(key_id, body.label, verifier, now.timestamp(), expiry.timestamp())
    metadata = KeyMetadata(id=key_id, label=body.label, created_at=now,
                           expires_at=expiry, revoked_at=None, status="active")
    return IssuedKey(metadata, secret)


def list_keys(*, limit: int = 50, offset: int = 0) -> list[KeyMetadata]:
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("Invalid pagination")
    now = _now()
    return [_metadata(row, now) for row in repository.page(limit, offset)]


def revoke(key_id: str) -> KeyMetadata | None:
    if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
        return None
    now = _now()
    row = repository.revoke(key_id, now.timestamp())
    return _metadata(row, now) if row else None


def active_key(key_id: str) -> KeyMetadata | None:
    """Fresh validity read for future session checks; a public ID is NOT a credential."""
    if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
        return None
    row = repository.get(key_id)
    if row is None:
        return None
    metadata = _metadata(row, _now())
    return metadata if metadata.status == "active" else None


def authenticate(secret: str) -> KeyMetadata | None:
    """Resolve a future login attempt, without issuing a session or renewing expiry."""
    if not isinstance(secret, str) or len(secret) != 84:
        return None
    match = _CREDENTIAL.fullmatch(secret)
    if match is None:
        return None
    row = repository.get(match[1], with_verifier=True)
    candidate = hashlib.sha256(secret.encode("ascii")).hexdigest()
    matches = hmac.compare_digest(candidate, row["verifier"] if row else "0" * 64)
    if not matches or row is None:
        return None
    # Re-read after verifier comparison so a revocation committed during that read
    # cannot be mistaken for an active key. Protected operations must check again.
    return active_key(row["id"])
