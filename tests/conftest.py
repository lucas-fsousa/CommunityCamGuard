"""Shared test fixtures.

Each test runs against a throwaway SQLite DB and a fixed dashboard secret, injected via
environment variables (which take precedence over `.env`) with the settings cache cleared so
the overrides take effect.
"""
from __future__ import annotations

import pytest

from backend.app import config


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv("AUTOSTART_SERVICES", "false")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()
