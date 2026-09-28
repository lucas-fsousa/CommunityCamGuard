"""Exercise the operator check against isolated ASGI storage, never localhost."""

import importlib.util
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import access_keys, auth
from backend.app.api.access_keys import router as keys_router
from backend.app.api.auth import router as auth_router


@pytest.fixture
def probe(monkeypatch):
    path = Path(__file__).parents[1] / "scripts" / "check_delegated_access.py"
    spec = importlib.util.spec_from_file_location("deployment_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    now = [datetime.now(UTC)]
    monkeypatch.setattr(access_keys, "_now", lambda: now[0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=time.monotonic,
        sleep=lambda seconds: now.__setitem__(0, now[0] + timedelta(seconds=seconds))))
    app = FastAPI(); app.include_router(auth_router); app.include_router(keys_router)
    # Permission checks happen before these diagnostic-only handlers.
    from fastapi import Depends
    for path in ("/api/media/streams", "/api/recordings"):
        app.add_api_route(path, lambda: {}, dependencies=[Depends(auth.require_auth)])
    from backend.app.api.settings import router as settings_router
    app.include_router(settings_router)
    return module, TestClient(app), auth.issue_token()


def test_probe_exercises_lifecycle_and_leaves_only_revoked_records(probe):
    module, client, owner = probe
    result = module.exercise(client, owner)
    assert result == dict(delegated_login=True, permission_denials=True, expiry=True, revocation=True)
    records = access_keys.list_keys()
    assert len(records) == 2 and all(record.status == "revoked" for record in records)
    assert {record.label for record in records} == {"Deployment check — expiry", "Deployment check — revocation"}


def test_mid_check_failure_still_revokes_created_key(probe, monkeypatch):
    module, client, owner = probe
    original = module.check
    def fail_guest_check(client, method, path, expected=200, token=None, body=None):
        if path == "/api/me" and token != owner:
            raise RuntimeError("simulated failure")
        return original(client, method, path, expected, token, body)
    monkeypatch.setattr(module, "check", fail_guest_check)
    with pytest.raises(RuntimeError, match="simulated failure"):
        module.exercise(client, owner)
    assert [record.status for record in access_keys.list_keys()] == ["revoked"]


def test_lost_creation_reply_reports_unknown_outcome_without_retry(probe, monkeypatch, capsys):
    module, client, owner = probe
    original = module.check
    creations = []

    def lose_reply(client, method, path, expected=200, token=None, body=None):
        response = original(client, method, path, expected, token, body)
        if method == "POST" and path == "/api/access-keys":
            creations.append(response.json()["metadata"]["id"])
            raise RuntimeError("simulated lost creation reply")
        return response

    monkeypatch.setattr(module, "check", lose_reply)
    with pytest.raises(RuntimeError, match="simulated lost creation reply"):
        module.exercise(client, owner)
    output = capsys.readouterr().out
    assert '"test_key_creation_outcome_unknown": true' in output
    assert '"review_deployment_check_keys": true' in output
    assert len(creations) == 1 and creations[0] not in output
    assert [record.status for record in access_keys.list_keys()] == ["active"]
