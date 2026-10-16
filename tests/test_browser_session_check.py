"""Browser operator probe must clean credentials on failure and reject false passes."""
import json
from types import SimpleNamespace

import pytest

from scripts import check_browser_session as probe
from scripts.browser_cdp import Browser


def test_cdp_skips_events_without_exposing_error_payload():
    class Socket:
        def __init__(self): self.sent = []
        frames = iter([{"method": "Target.event"}, {"id": 1, "result": {"ok": True}},
                       {"id": 2, "error": {"message": "private-value"}}])
        def send(self, message): self.sent.append(json.loads(message))
        def recv(self, timeout): return json.dumps(next(self.frames))
    browser = Browser(Socket())
    assert browser.call("test") == {"ok": True}
    with pytest.raises(RuntimeError, match=r"^Browser command failed$"):
        browser.call("test", session="tab")
    assert browser.socket.sent[1]["sessionId"] == "tab"


@pytest.mark.parametrize("mode", ["revoke", "expiry", "logout"])
def test_failed_browser_setup_revokes_only_known_test_key(monkeypatch, mode):
    calls = []
    def check(client, method, path, expected=200, token=None, body=None):
        calls.append(path)
        assert token == "owner"
        if path == "/api/access-keys":
            assert body["permissions"] == [] and body["expires_at"]
            return SimpleNamespace(json=lambda: {"metadata": {"id": "test-id"}, "secret": "private"})
        assert path == "/api/access-keys/test-id/revoke"
    monkeypatch.setattr(probe, "check", check)
    class FailedBrowser:
        def page(self, width): raise RuntimeError("No browser")
    with pytest.raises(RuntimeError, match="No browser"):
        probe.exercise(None, "owner", "https://example", FailedBrowser(), mode, 375)
    assert calls == ["/api/access-keys", "/api/access-keys/test-id/revoke"]


def test_wait_pages_requires_every_tab(monkeypatch):
    clock = [0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(probe.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 1))
    browser = SimpleNamespace(evaluate=lambda page, expression: page == "first")
    with pytest.raises(RuntimeError, match="deadline"):
        probe.wait_pages(browser, ["first", "second"], "test", 2)
