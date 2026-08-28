from __future__ import annotations

import sqlite3

from backend.app.config import get_settings
from backend.app.drivers.yoosee import account_store
from backend.app.drivers.yoosee.p2p.account import AccountCredentials, AccountSession


def _credentials() -> AccountCredentials:
    return AccountCredentials.from_password(
        account_type="email",
        account="person@example.invalid",
        password="test-only-password",
        unique_id="00000000-0000-4000-8000-000000000000",
    )


def _session(token: bytes = bytes(range(64))) -> AccountSession:
    access_id = "-12345"
    return AccountSession(
        access_id=access_id,
        access_token=token,
        common={"accessId": access_id, "accessToken": token[:48].hex()},
        headers={"x-iotvideo-accessid": access_id},
        expire_time=123,
        terminal_id="-98765",
        user_id="19088743",
    )


def test_vendor_account_round_trip_is_encrypted_at_rest():
    credentials = _credentials()
    session = _session()
    saved = account_store.save_account(credentials, session)
    loaded = account_store.get_account()

    assert loaded == saved
    assert account_store.has_account() is True
    with sqlite3.connect(get_settings().db_path) as conn:
        encrypted = bytes(
            conn.execute(
                "SELECT secret_enc FROM vendor_accounts WHERE provider = ?",
                (account_store.PROVIDER,),
            ).fetchone()[0]
        )
    assert credentials.account.encode() not in encrypted
    assert credentials.password_md5.encode() not in encrypted
    assert session.access_token.hex().encode() not in encrypted
    assert session.terminal_id.encode() not in encrypted


def test_session_refresh_rotates_token_without_replacing_account_credentials():
    credentials = _credentials()
    first = account_store.save_account(credentials, _session())
    second = account_store.update_session(_session(bytes(reversed(range(64)))))

    assert second.credentials == credentials
    assert second.session.access_token == bytes(reversed(range(64)))
    assert second.created_at == first.created_at


def test_delete_account_removes_only_vendor_account_record():
    account_store.save_account(_credentials(), _session())
    account_store.delete_account()

    assert account_store.has_account() is False
    assert account_store.get_account() is None
