from types import SimpleNamespace

import pytest

from scripts import check_browser_media as probe


@pytest.mark.parametrize("camera", ["", "cam_'", "../secret", "other"])
def test_rejects_invalid_camera_before_any_browser_or_key(camera):
    with pytest.raises(ValueError):
        probe.exercise(None, None, None, None, camera, "expiry")


def test_isolation_source_has_only_validated_camera_substitution():
    camera = "cam_" + "a" * 24
    source = probe.isolation_source(camera)
    assert "__CAMERA_ID__" not in source
    assert source.count(camera) == 1


def test_browser_failure_cleans_known_live_only_short_lived_key(monkeypatch):
    revoked = []
    def check(client, method, path, expected=200, token=None, body=None):
        if path == "/api/access-keys":
            assert body["permissions"] == ["live"] and body["expires_at"]
            return SimpleNamespace(json=lambda: {"metadata": {"id": "test"}, "secret": "private"})
        revoked.append(path)
    monkeypatch.setattr(probe, "check", check)
    class Browser:
        def page(self, width): raise RuntimeError("test failure")
    with pytest.raises(RuntimeError, match="test failure"):
        probe.exercise(None, "owner", "https://example", Browser(), "cam_" + "a" * 24, "expiry")
    assert revoked == ["/api/access-keys/test/revoke"]


def test_missing_isolation_refuses_browser_login_and_revokes_key(monkeypatch):
    revoked, evaluations = [], []
    def check(client, method, path, expected=200, token=None, body=None):
        if path == "/api/access-keys":
            return SimpleNamespace(json=lambda: {"metadata": {"id": "test"}, "secret": "private"})
        revoked.append(path)
    monkeypatch.setattr(probe, "check", check)
    monkeypatch.setattr(probe, "wait_pages", lambda *args: None)
    class Browser:
        def page(self, width): return "page"
        def call(self, *args): pass
        def evaluate(self, page, expression):
            evaluations.append(expression)
            return False
    with pytest.raises(RuntimeError, match="Media isolation not installed"):
        probe.exercise(None, "owner", "https://example", Browser(), "cam_" + "a" * 24, "expiry")
    assert len(evaluations) == 1 and "requestSubmit" not in evaluations[0]
    assert revoked == ["/api/access-keys/test/revoke"]
