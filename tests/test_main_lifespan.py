"""Cover main.py's service-startup orchestration (the lifespan autostart block), which the other
main tests skip by running with AUTOSTART_SERVICES=false. Services are faked so nothing real spawns.
"""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from backend.app import config, main
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.source_registry import SourceRegistry
from backend.app.recording.process_lifecycle import RecorderCleanupUnconfirmed


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

        def ensure_external_config(self, *a, **k):
            events.append(f"{name}.ensure_external_config")
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
    monkeypatch.setattr(main, "ControlSessions", _fake_service("controls", events))
    monkeypatch.setattr(main, "AddressRecovery", _fake_service("recovery", events))
    class Native(_fake_service("native", events)):
        def __init__(self, *args, **kwargs):
            self.sources = SourceRegistry()
    monkeypatch.setattr(main, "NativeSourceRuntime", Native)


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
    for svc in ("rec", "storage", "retention", "warmer", "controls"):
        assert f"{svc}.start" in events
    # cleanly stopped on shutdown
    for svc in ("controls", "warmer", "retention", "storage", "native", "rec", "media"):
        assert f"{svc}.stop" in events
    config.get_settings.cache_clear()


def test_control_session_warmup_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AUTOSTART_SERVICES", "true")
    monkeypatch.setenv("CONTROL_SESSION_WARMUP", "false")
    config.get_settings.cache_clear()
    events = []
    _patch_services(monkeypatch, events)
    with TestClient(main.app):
        pass
    assert "controls.start" not in events and "controls.stop" in events
    config.get_settings.cache_clear()


def test_lifespan_external_go2rtc_syncs_without_spawning(monkeypatch):
    monkeypatch.setenv("AUTOSTART_SERVICES", "true")
    monkeypatch.setenv("MANAGE_GO2RTC", "false")   # go2rtc is its own container
    config.get_settings.cache_clear()
    events: list[str] = []
    _patch_services(monkeypatch, events)
    with TestClient(main.app):
        pass
    assert "media.ensure_external_config" in events
    assert "media.write_config" not in events and "media.reload_external" not in events
    assert "media.start" not in events
    config.get_settings.cache_clear()


@pytest.mark.parametrize("failure", ["controls", "native", "rec", "media"])
def test_shutdown_attempts_other_services_after_unconfirmed_cleanup(monkeypatch, failure):
    events = []
    _patch_services(monkeypatch, events)

    class Failed(_fake_service(failure, events)):
        def __init__(self, *args, **kwargs):
            self.sources = SourceRegistry()
        def stop(self):
            events.append(f"{failure}.stop")
            raise RecorderCleanupUnconfirmed("synthetic cleanup unconfirmed")

    attribute = {"controls": "ControlSessions", "native": "NativeSourceRuntime",
                 "rec": "Recorder", "media": "Go2rtc"}[failure]
    monkeypatch.setattr(main, attribute, Failed)
    with pytest.raises(RecorderCleanupUnconfirmed):
        with TestClient(main.app):
            pass
    assert [event for event in events if event.endswith(".stop")] == [
        "controls.stop", "recovery.stop", "warmer.stop", "retention.stop",
        "storage.stop", "native.stop", "rec.stop", "media.stop",
    ]


def test_native_runtime_stays_idle_and_is_replaced_on_a_new_lifespan(monkeypatch):
    _patch_services(monkeypatch, [])
    monkeypatch.setattr(main, "NativeSourceRuntime", NativeSourceRuntime)
    with TestClient(main.app):
        first = main.app.state.native_runtime
        assert main.app.state.native_sources is first.sources
        assert first.sources.camera_ids() == () and not first._executor._threads
    assert first._stopped
    with TestClient(main.app):
        second = main.app.state.native_runtime
        assert second is not first and not second._executor._threads
    assert second._stopped


def test_shutdown_keeps_asgi_loop_runnable_while_source_cleanup_waits(monkeypatch):
    _patch_services(monkeypatch, [])
    stopping, loop_ran = threading.Event(), threading.Event()
    loop_thread = []
    class Native:
        def __init__(self, *args, **kwargs):
            self.sources = SourceRegistry()
        def stop(self):
            assert threading.get_ident() != loop_thread[0]
            stopping.set()
            assert loop_ran.wait(1)
    monkeypatch.setattr(main, "NativeSourceRuntime", Native)
    async def run():
        loop_thread.append(threading.get_ident())
        async def witness():
            while not stopping.is_set():
                await asyncio.sleep(0.001)
            loop_ran.set()
        witness_task = asyncio.create_task(witness())
        async with main.lifespan(main.app):
            pass
        await witness_task
    asyncio.run(run())
    assert loop_ran.is_set()
