from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import renewal
from backend.app.drivers.yoosee.p2p.account import AccountSession
from backend.app.drivers.yoosee.p2p.contracts import InitInfoRejectedError


def _enrollment(token: bytes) -> P2PEnrollment:
    return P2PEnrollment(
        "7000000002", 123, token, "ab" * 64, "created", "updated", "cam_" + "1" * 24
    )


def _session(token: bytes) -> AccountSession:
    return AccountSession(
        access_id="456",
        access_token=token,
        common={},
        headers={"x-iotvideo-accessid": "456"},
        expire_time=None,
        terminal_id="1",
        user_id="2",
    )


def test_stale_access_is_refreshed_and_operation_retried_once(monkeypatch):
    old = _enrollment(bytes(range(64)))
    new_token = bytes(reversed(range(64)))
    refreshed = _enrollment(new_token)
    calls = []

    def operation(enrollment):
        calls.append(("operation", enrollment.access_token))
        if enrollment.access_token == old.access_token:
            raise InitInfoRejectedError(0x216B)
        return "ok"

    monkeypatch.setattr(renewal.p2p, "get_enrollment", lambda _device_id: old)
    monkeypatch.setattr(
        renewal.account_store,
        "get_account",
        lambda: SimpleNamespace(session=_session(old.access_token)),
    )
    monkeypatch.setattr(renewal, "refresh_account_session", lambda _current: _session(new_token))
    monkeypatch.setattr(
        renewal.account_store,
        "update_session",
        lambda session: calls.append(("account", session.access_token)),
    )

    def fake_upsert(device_id, **values):
        calls.append(("enrollment", device_id, values))
        return refreshed

    monkeypatch.setattr(renewal.p2p, "upsert_enrollment", fake_upsert)

    assert renewal.run_with_fresh_access(old, operation) == "ok"
    assert [name for name, *_rest in calls] == [
        "operation",
        "account",
        "enrollment",
        "operation",
    ]
    stored_values = calls[2][2]
    assert stored_values["camera_id"] == old.camera_id
    assert stored_values["dev_token"] == old.dev_token


def test_concurrent_refresh_result_is_reused_without_second_cloud_request(monkeypatch):
    old = _enrollment(bytes(range(64)))
    current = _enrollment(bytes(reversed(range(64))))
    attempts = []

    def operation(enrollment):
        attempts.append(enrollment.access_token)
        if enrollment is old:
            raise InitInfoRejectedError(0x216B)
        return "ok"

    monkeypatch.setattr(renewal.p2p, "get_enrollment", lambda _device_id: current)
    monkeypatch.setattr(
        renewal.account_store,
        "get_account",
        lambda: (_ for _ in ()).throw(AssertionError("duplicate refresh")),
    )

    assert renewal.run_with_fresh_access(old, operation) == "ok"
    assert attempts == [old.access_token, current.access_token]


def test_non_stale_rejection_is_not_refreshed(monkeypatch):
    old = _enrollment(bytes(range(64)))
    monkeypatch.setattr(
        renewal.account_store,
        "get_account",
        lambda: (_ for _ in ()).throw(AssertionError("refresh attempted")),
    )

    with pytest.raises(InitInfoRejectedError):
        renewal.run_with_fresh_access(
            old, lambda _enrollment: (_ for _ in ()).throw(InitInfoRejectedError(123))
        )


def test_stuck_device_session_lock_fails_with_a_bounded_error(monkeypatch):
    enrollment = _enrollment(bytes(range(64)))
    lock = renewal._session_lock(enrollment.device_id)
    lock.acquire()
    monkeypatch.setattr(renewal, "SESSION_LOCK_TIMEOUT_SECONDS", 0.01)
    try:
        with pytest.raises(renewal.P2PProbeError, match="did not release"):
            renewal.run_with_fresh_access(enrollment, lambda _current: "unexpected")
    finally:
        lock.release()
