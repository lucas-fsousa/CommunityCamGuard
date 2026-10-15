"""Operator acceptance helper: no real keys, sockets or camera operations."""
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close

from scripts import check_public_channels as probe


def test_closed_channel_must_carry_policy_violation():
    class Socket:
        def recv(self, timeout):
            assert timeout == 7
            raise ConnectionClosedError(Close(1008, ""), Close(1008, ""), True)
    assert probe.expect_invalidated(Socket()) >= 0


@pytest.mark.parametrize("code", [None, 1000, 1011])
def test_abnormal_or_unrelated_close_is_not_claimed_as_revocation(code):
    class Socket:
        def recv(self, timeout):
            frame = None if code is None else Close(code, "")
            raise ConnectionClosedError(frame, frame, None if frame is None else True)
    with pytest.raises(RuntimeError): probe.expect_invalidated(Socket())


@pytest.mark.parametrize("status", [301, 401, 500])
def test_non_permission_handshake_failure_is_not_accepted(status):
    def connect(*args, **kwargs):
        raise InvalidStatus(SimpleNamespace(status_code=status, reason_phrase="test"))
    with pytest.raises(RuntimeError): probe.expect_denied("wss://example", {}, connect)


def test_known_keys_are_revoked_when_opening_channel_fails(monkeypatch):
    created, revoked = [], []
    def check(client, method, path, expected=200, token=None, body=None):
        if path == "/api/access-keys":
            key = str(len(created))
            created.append(key)
            return SimpleNamespace(json=lambda: {"metadata": {"id": key}, "secret": "private"})
        if path == "/api/login":
            return SimpleNamespace(cookies={"ccg_session": "private-token"})
        assert path.endswith("/revoke") and token == "owner"
        revoked.append(path.split("/")[-2])
    monkeypatch.setattr(probe, "check", check)
    monkeypatch.setattr(probe, "expect_denied", lambda *args: None)
    def connect(*args, **kwargs): raise TimeoutError
    with pytest.raises(TimeoutError):
        probe.exercise(None, "owner", "https://example", "cam_" + "a" * 24, connect)
    assert created == revoked == ["0", "1", "2"]


def test_unknown_creation_outcome_is_reported_without_retry(monkeypatch, capsys):
    calls = []
    def check(*args, **kwargs):
        calls.append(True)
        raise TimeoutError
    monkeypatch.setattr(probe, "check", check)
    with pytest.raises(TimeoutError):
        probe.exercise(None, "owner", "https://example", "cam_" + "a" * 24)
    assert calls == [True]
    assert '"test_key_creation_outcome_unknown": true' in capsys.readouterr().out


def test_socket_resource_caps_and_no_environment_proxy(monkeypatch):
    def connect(uri, *, proxy=True): pass
    monkeypatch.setattr(probe, "connect", connect)
    options = probe.socket_options("https://example", "private")
    assert options["proxy"] is None
    assert options["max_size"] == 4096 and options["max_queue"] == 1
    assert options["open_timeout"] == 5 and options["close_timeout"] == 1


def test_old_websockets_without_proxy_discovery_does_not_receive_unsupported_kwarg(monkeypatch):
    monkeypatch.setattr(probe, "connect", lambda uri: None)
    assert "proxy" not in probe.socket_options("https://example", "private")
