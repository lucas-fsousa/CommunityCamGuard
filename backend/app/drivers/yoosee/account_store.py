"""Encrypted persistence for the Yoosee account and renewable session."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from ...db import connect
from ...db.registry import _decrypt, _encrypt
from .p2p.account import AccountCredentials, AccountSession

log = logging.getLogger(__name__)

PROVIDER = "yoosee-gwell"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS vendor_accounts (
    provider    TEXT PRIMARY KEY,
    secret_enc  BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class StoredVendorAccount:
    credentials: AccountCredentials
    session: AccountSession
    created_at: str
    updated_at: str


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _serialize(credentials: AccountCredentials, session: AccountSession) -> str:
    return json.dumps(
        {
            "version": 1,
            "credentials": {
                "account_type": credentials.account_type,
                "account": credentials.account,
                "password_md5": credentials.password_md5,
                "unique_id": credentials.unique_id,
                "mobile_area": credentials.mobile_area,
                "language": credentials.language,
                "region": credentials.region,
                "area": credentials.area,
            },
            "session": {
                "access_id": session.access_id,
                "access_token": session.access_token.hex(),
                "common": dict(session.common),
                "headers": dict(session.headers),
                "expire_time": session.expire_time,
                "terminal_id": session.terminal_id,
                "user_id": session.user_id,
            },
        },
        separators=(",", ":"),
    )


def _deserialize(payload: str) -> tuple[AccountCredentials, AccountSession]:
    root = json.loads(payload)
    if not isinstance(root, dict) or root.get("version") != 1:
        raise ValueError("unsupported vendor account record")
    credentials_data = root.get("credentials")
    session_data = root.get("session")
    if not isinstance(credentials_data, dict) or not isinstance(session_data, dict):
        raise ValueError("incomplete vendor account record")
    credentials = AccountCredentials(
        account_type=credentials_data["account_type"],
        account=credentials_data["account"],
        password_md5=credentials_data["password_md5"],
        unique_id=credentials_data["unique_id"],
        mobile_area=credentials_data["mobile_area"],
        language=credentials_data["language"],
        region=credentials_data["region"],
        area=credentials_data["area"],
    )
    token_hex = session_data["access_token"]
    if not isinstance(token_hex, str):
        raise ValueError("invalid vendor account token")
    session = AccountSession(
        access_id=session_data["access_id"],
        access_token=bytes.fromhex(token_hex),
        common=session_data["common"],
        headers=session_data["headers"],
        expire_time=session_data.get("expire_time"),
        terminal_id=session_data["terminal_id"],
        user_id=session_data["user_id"],
    )
    return credentials, session


def save_account(
    credentials: AccountCredentials,
    session: AccountSession,
) -> StoredVendorAccount:
    """Atomically persist one account/session as an encrypted payload."""

    # Construction validates all fields before the old durable record can be replaced.
    encoded = _serialize(credentials, session)
    _deserialize(encoded)
    init_db()
    now = _now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT created_at FROM vendor_accounts WHERE provider = ?", (PROVIDER,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO vendor_accounts (provider, secret_enc, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                secret_enc=excluded.secret_enc,
                updated_at=excluded.updated_at
            """,
            (PROVIDER, _encrypt(encoded), created_at, now),
        )
    return StoredVendorAccount(credentials, session, created_at, now)


def get_account() -> StoredVendorAccount | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM vendor_accounts WHERE provider = ?", (PROVIDER,)
        ).fetchone()
    if row is None:
        return None
    try:
        credentials, session = _deserialize(
            _decrypt(row["secret_enc"], label="vendor account")
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        log.warning("stored vendor account is invalid")
        return None
    return StoredVendorAccount(
        credentials=credentials,
        session=session,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def update_session(session: AccountSession) -> StoredVendorAccount:
    stored = get_account()
    if stored is None:
        raise ValueError("vendor account is not enrolled")
    return save_account(stored.credentials, session)


def has_account() -> bool:
    init_db()
    with connect() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM vendor_accounts WHERE provider = ?", (PROVIDER,)
            ).fetchone()
            is not None
        )


def delete_account() -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM vendor_accounts WHERE provider = ?", (PROVIDER,))
