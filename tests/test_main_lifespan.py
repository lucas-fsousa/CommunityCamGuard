"""Cover main.py's service-startup orchestration (the lifespan autostart block), which the other
main tests skip by running with AUTOSTART_SERVICES=false. Services are faked so nothing real spawns.
"""
from fastapi.testclient import TestClient

from backend.app import config, main


def _fake_service(name, events):
    class F:
        def __init__(self, *a, **k):
            pass

        def start(self, *a, **k):
            events.append(f"{name}.start")

        def write_config(self, *a, **k):
            events.append(f"{name}.write_config")

        def reload_external(self, *a, **k):
            events.append(f"{name}.reload_external")
            return True

        def wait_healthy(self, *a, **k):
            return True

        def stop(self):
            events.append(f"{name}.stop")

    return F


def _patch_services(monkeypatch, events):
    monkeypatch.setattr(main, "Go2rtc", _fake_service("media", events))
    monkeypatch.setattr(main, "Recorder", _fake_service("rec", events))
    monkeypatch.setattr(main, "StorageMonitor", _fake_service("storage", events))
    monkeypatch.setattr(main, "RetentionCleaner", _fake_service("retention", events))
    monkeypatch.setattr(main, "Warmer", _fake_service("warmer", events))


def test_lifespan_autostarts_then_stops_all_services(monkeypatch):
    monkeypatch.setenv("AUTOSTART_SERVICES", "true")
    monkeypatch.setenv("MANAGE_GO2RTC", "true")   # we own the binary -> media.start()
    config.get_settings.cache_clear()
    events: list[str] = []
    _patch_services(monkeypatch, events)
    with TestClient(main.app):
        pass                                       # enter + exit the lifespan
    # started (owned go2rtc -> start, then recorder/storage/retention/warmer)
    assert "media.start" in events
    for svc in ("rec", "storage", "retention", "warmer"):
        assert f"{svc}.start" in events
    # cleanly stopped on shutdown
    for svc in ("warmer", "retention", "storage", "rec", "media"):
        assert f"{svc}.stop" in events
    config.get_settings.cache_clear()


def test_lifespan_external_go2rtc_writes_and_reloads_instead_of_spawning(monkeypatch):
    monkeypatch.setenv("AUTOSTART_SERVICES", "true")
    monkeypatch.setenv("MANAGE_GO2RTC", "false")   # go2rtc is its own container
    config.get_settings.cache_clear()
    events: list[str] = []
    _patch_services(monkeypatch, events)
    with TestClient(main.app):
        pass
    assert "media.write_config" in events and "media.reload_external" in events
    assert events.index("media.write_config") < events.index("media.reload_external")
    assert "media.start" not in events
    config.get_settings.cache_clear()
