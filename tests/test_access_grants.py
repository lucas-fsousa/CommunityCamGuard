"""Delegated grants and nullable expiry, without camera I/O or production DB writes."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import access_keys, auth
from backend.app.db import access_keys as repository
from backend.app.db import connect
from backend.app.session_permissions import temporary_http_allowed


def test_non_expiring_key_remains_revocable(monkeypatch):
    key = access_keys.create(access_keys.CreateKey(label="Permanent guest", expires_at=None, permissions=("ptz",)))
    token = auth.issue_temporary_token(key.secret)
    monkeypatch.setattr(access_keys, "_now", lambda: datetime.now(UTC) + timedelta(days=3650))
    assert access_keys.authenticate(key.secret).expires_at is None
    assert auth.token_principal(token).permissions == ("ptz",)
    assert not auth.token_principal(token).can_manage
    access_keys.revoke(key.metadata.id)
    assert not auth.verify_token(token)


@pytest.mark.parametrize("grants", [("admin",), ("ptz", "ptz"), ("*",), ("future_control",), "ptz"])
def test_unknown_duplicate_or_unbounded_grants_rejected(grants):
    with pytest.raises(ValidationError):
        access_keys.CreateKey(label="Guest", expires_at=None, permissions=grants)


def test_migrates_legacy_records_without_resetting_secrets_or_revocation():
    with connect() as conn:
        conn.execute("""CREATE TABLE dashboard_access_keys (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, verifier TEXT NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL CHECK(expires_at > created_at), revoked_at REAL)""")
        conn.execute("INSERT INTO dashboard_access_keys VALUES (?, ?, ?, ?, ?, ?)",
                     ("a" * 32, "Old", "b" * 64, 100, 200, 150))
    row = repository.get("a" * 32, with_verifier=True)
    assert row == dict(id="a" * 32, label="Old", verifier="b" * 64, created_at=100,
                       expires_at=200, revoked_at=150, permissions=["live", "recordings"])
    # Idempotent migration and nullable expiry coexist with old rows.
    key = access_keys.create(access_keys.CreateKey(label="New", expires_at=None, permissions=()))
    assert repository.get("a" * 32, with_verifier=True) == row
    assert access_keys.active_key(key.metadata.id).permissions == ()


@pytest.mark.parametrize("grant,method,path,control", [
    ("live", "GET", "/api/media/streams", None),
    ("recordings", "GET", "/api/recordings/download", None),
    ("recordings", "POST", "/api/recordings/prepare", None),
    ("ptz", "POST", "/api/cameras/{camera_id}/ptz", None),
    ("reboot", "POST", "/api/cameras/{camera_id}/reboot", None),
    ("intercom", "POST", "/api/cameras/{camera_id}/intercom/messages", None),
    ("white_light", "PUT", "/api/cameras/{camera_id}/controls/{control_key}", "white_light"),
    ("alarm_voice", "GET", "/api/cameras/{camera_id}/controls/{control_key}/options", "alarm_voice"),
])
def test_permission_matrix(grant, method, path, control):
    assert temporary_http_allowed(method, path, (grant,), control)
    assert not temporary_http_allowed(method, path, (), control)
    assert not temporary_http_allowed(method, path, ("orientation",), control)
    assert not temporary_http_allowed("DELETE", path, (grant,), control)


def test_actual_dependency_reads_persisted_grants_and_never_cookie_claims():
    key = access_keys.create(access_keys.CreateKey(label="Limited", expires_at=None, permissions=("orientation",)))
    token = auth.issue_temporary_token(key.secret)
    app = FastAPI()
    @app.put("/api/cameras/{camera_id}/controls/{control_key}", dependencies=[Depends(auth.require_auth)])
    def control(camera_id: str, control_key: str):
        return {"ok": True}
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.put("/api/cameras/test/controls/orientation").status_code == 200
    assert client.put("/api/cameras/test/controls/siren_pulse").status_code == 403
    assert client.put("/api/cameras/test/controls/future_control").status_code == 403
    assert not auth.verify_permission_token(token, "live")
    assert not auth.verify_permission_token(token, "intercom")
    assert auth.verify_permission_token(token, "orientation")
    access_keys.revoke(key.metadata.id)
    assert client.put("/api/cameras/test/controls/orientation").status_code == 401
