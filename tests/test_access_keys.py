"""Isolated lifecycle foundation: no production DB, login activation or cameras."""

import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import access_keys, auth
from backend.app.api.auth import router
from backend.app.config import get_settings
from backend.app.db import access_keys as repository


@pytest.fixture
def clock(monkeypatch):
    now = [datetime(2026, 9, 24, 12, tzinfo=UTC)]
    monkeypatch.setattr(access_keys, "_now", lambda: now[0])
    return now


def issue(clock, label="Guest"):
    return access_keys.create(access_keys.CreateKey(label=label, expires_at=clock[0] + timedelta(hours=1)))


def test_one_time_random_secret_never_stored_or_listed(clock):
    first, second = issue(clock, " Guest "), issue(clock)
    assert first.secret != second.secret
    assert first.metadata.id != second.metadata.id
    assert len(first.secret) == 84
    assert first.metadata.label == "Guest"
    assert first.secret not in repr(first)
    assert access_keys.authenticate(first.secret) == first.metadata
    assert access_keys.active_key(first.metadata.id) == first.metadata
    records = access_keys.list_keys()
    assert len(records) == 2
    for record in records:
        assert set(record.model_dump()) == {"id", "label", "created_at", "expires_at", "revoked_at", "status"}
        assert first.secret not in record.model_dump_json()
    assert "verifier" not in repository.get(first.metadata.id)
    stored = repository.get(first.metadata.id, with_verifier=True)
    assert len(stored["verifier"]) == 64 and stored["verifier"] != first.secret
    assert first.secret.encode() not in get_settings().db_path.read_bytes()


@pytest.mark.parametrize("expiration", [None, True, 123, 123.5, "123", "infinity", "2026-09-25", "2026-09-25T12:00:00"])
def test_expiration_requires_explicit_timezone(expiration):
    with pytest.raises(ValidationError):
        access_keys.CreateKey(label="Guest", expires_at=expiration)


@pytest.mark.parametrize("label", [None, True, 12, "", "   ", "x" * 81])
def test_label_validation(clock, label):
    with pytest.raises(ValidationError):
        access_keys.CreateKey(label=label, expires_at=clock[0] + timedelta(hours=1))


def test_utc_conversion_exact_boundary_and_no_sliding_expiry(clock):
    key = access_keys.create(access_keys.CreateKey(label="Guest", expires_at="2026-09-24T10:00:00-03:00"))
    expires = datetime(2026, 9, 24, 13, tzinfo=UTC)
    assert key.metadata.expires_at == expires and key.metadata.expires_at.tzinfo == UTC
    clock[0] = expires - timedelta(microseconds=1)
    assert access_keys.authenticate(key.secret).expires_at == expires
    clock[0] = expires
    assert access_keys.authenticate(key.secret) is None
    assert access_keys.active_key(key.metadata.id) is None
    assert access_keys.list_keys()[0].status == "expired"
    with pytest.raises(ValueError, match="future"):
        access_keys.create(access_keys.CreateKey(label="Guest", expires_at=expires))
    assert len(access_keys.list_keys()) == 1


def test_unknown_privilege_fields_rejected(clock):
    with pytest.raises(ValidationError):
        access_keys.CreateKey(label="Guest", expires_at=clock[0], can_manage=True)


def test_revocation_is_persistent_idempotent_and_independent(clock):
    key, other = issue(clock), issue(clock)
    revoked = access_keys.revoke(key.metadata.id)
    assert revoked.status == "revoked" and revoked.revoked_at == clock[0]
    clock[0] += timedelta(minutes=5)
    assert access_keys.revoke(key.metadata.id) == revoked
    assert access_keys.authenticate(key.secret) is None
    assert access_keys.active_key(key.metadata.id) is None
    assert access_keys.authenticate(other.secret) is not None
    # Fresh module/connection has no process-local revocation state to lose.
    importlib.reload(repository)
    assert access_keys.active_key(key.metadata.id) is None
    clock[0] += timedelta(days=1)
    assert access_keys.revoke(key.metadata.id).status == "revoked"


def test_concurrent_revocations_preserve_first_timestamp(clock):
    key = issue(clock)
    def revoke(index):
        return repository.revoke(key.metadata.id, clock[0].timestamp() + index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(revoke, (1, 2)))
    assert records[0]["revoked_at"] == records[1]["revoked_at"]
    assert access_keys.active_key(key.metadata.id) is None


def test_revocation_during_credential_verification_fails(clock, monkeypatch):
    key = issue(clock)
    original = repository.get
    def racing_get(key_id, *, with_verifier=False):
        row = original(key_id, with_verifier=with_verifier)
        if with_verifier:
            repository.revoke(key_id, clock[0].timestamp())
        return row
    monkeypatch.setattr(repository, "get", racing_get)
    assert access_keys.authenticate(key.secret) is None


@pytest.mark.parametrize("value", [None, 123, "", "x" * 100000, "../test.db", "' OR 1=1 --", "é" * 84])
def test_malformed_credentials_do_not_query_database(value, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Malformed credential reached storage")
    monkeypatch.setattr(repository, "get", forbidden)
    assert access_keys.authenticate(value) is None
    assert access_keys.active_key(value) is None
    assert access_keys.revoke(value) is None


def test_wrong_secret_and_unknown_identifier_denied(clock):
    key = issue(clock)
    altered = key.secret[:-1] + ("A" if key.secret[-1] != "A" else "B")
    assert access_keys.authenticate(altered) is None
    assert access_keys.authenticate("ccg_tmp_" + "0" * 32 + "." + "A" * 43) is None
    assert access_keys.revoke("0" * 32) is None
    assert access_keys.authenticate(key.metadata.id) is None


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (True, 0), (1, -1), (1, True)])
def test_bounded_pagination(limit, offset):
    with pytest.raises(ValueError):
        access_keys.list_keys(limit=limit, offset=offset)


def test_paginated_metadata_order_and_no_secret(clock):
    first = issue(clock)
    clock[0] += timedelta(seconds=1)
    second = issue(clock)
    assert access_keys.list_keys(limit=1) == [second.metadata]
    assert access_keys.list_keys(limit=1, offset=1) == [first.metadata]
    assert access_keys.list_keys(offset=2) == []


def test_storage_failure_is_not_success(clock, monkeypatch):
    key = issue(clock)
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("test failure")
    monkeypatch.setattr(repository, "get", unavailable)
    with pytest.raises(sqlite3.Error):
        access_keys.authenticate(key.secret)
    with pytest.raises(sqlite3.Error):
        access_keys.active_key(key.metadata.id)
    monkeypatch.setattr(repository, "insert", unavailable)
    with pytest.raises(sqlite3.Error):
        issue(clock)


def test_temporary_login_and_claims_remain_disabled(clock):
    key = issue(clock)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/login", json={"key": key.secret}).status_code == 401
        assert not client.cookies.get(auth.COOKIE_NAME)
        assert client.get("/api/me").json()["authenticated"] is False
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 200
        assert client.get("/api/me").json()["can_manage"] is True
    forged_kind = auth._serializer().dumps({"v": 1, "authentication": "temporary", "sid": "a" * 32,
                                          "key_id": key.metadata.id})
    assert not auth.verify_token(forged_kind)
