"""Hub mutation exclusion and deferred convergence; no real services/network."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.api import media as media_routes
from backend.app.db import registry
from backend.app.media import go2rtc
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.owned_retirement import OwnedRetirementCoordinator
from backend.app.media.reconciliation import ReconciliationBusy, ReconciliationGuard
from backend.app.media.viewer_gate import ViewerGate
from backend.app.recording.ingress_gate import RecordingIngressGate
from backend.app.services.address_recovery import AddressRecovery
from backend.app.services.camera_runtime import resync_services


class FastGuard(ReconciliationGuard):
    def exclusive(self, *, timeout=0.01):
        return super().exclusive(timeout=timeout)


def test_owned_binding_composition_shares_one_guard_without_manual_reassignment():
    bindings = OwnedIngressBindings()
    engine = go2rtc.Go2rtc(owned_ingress=bindings)
    recordings = RecordingIngressGate(lock=threading.Lock(), retire=lambda _: None, has_process=lambda _: False)
    coordinator = OwnedRetirementCoordinator(viewers=ViewerGate(), recordings=recordings, bindings=bindings)
    assert engine.reconciliation is coordinator.reconciliation is bindings.reconciliation
    with pytest.raises(ValueError, match="share"):
        OwnedRetirementCoordinator(viewers=ViewerGate(), recordings=recordings, bindings=bindings,
                                    reconciliation=ReconciliationGuard())


@pytest.mark.parametrize("method", ["write_config", "write_config_if_changed", "start", "stop",
                                   "restart", "reload_external", "ensure_external_config", "release_live_producers"])
def test_all_public_mutations_reject_before_io_when_another_thread_owns_guard(tmp_path, monkeypatch, method):
    engine = go2rtc.Go2rtc(manage=False, config_path=tmp_path / "config.json")
    engine.reconciliation = FastGuard()
    io = Mock(side_effect=AssertionError("hub mutation bypassed guard"))
    monkeypatch.setattr(go2rtc.urllib.request, "urlopen", io)
    args = ("cam_" + "a" * 24,) if method == "release_live_producers" else ()
    with ThreadPoolExecutor(max_workers=1) as pool, engine.reconciliation.exclusive():
        future = pool.submit(getattr(engine, method), *args)
        with pytest.raises(ReconciliationBusy):
            future.result(timeout=1)
    io.assert_not_called()
    assert not engine.config_path.exists()


def test_restart_holds_exclusion_across_write_and_reload_not_just_each_subcall(monkeypatch):
    engine = go2rtc.Go2rtc(manage=False)
    entered, release, attempting = threading.Event(), threading.Event(), threading.Event()
    events = []
    def write(_cameras, *, only_if_changed=False):
        if not only_if_changed:
            events.append("write")
            entered.set()
            assert release.wait(2)
        else:
            events.append("other-write")
        return True
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
    def reload(*args, **kwargs):
        events.append("reload")
        return Response()
    monkeypatch.setattr(engine, "_write_config", write)
    monkeypatch.setattr(go2rtc.urllib.request, "urlopen", reload)
    def concurrent_write():
        attempting.set()
        return engine.write_config_if_changed([])
    with ThreadPoolExecutor(max_workers=2) as pool:
        restart = pool.submit(engine.restart, [])
        try:
            assert entered.wait(1)
            other = pool.submit(concurrent_write)
            assert attempting.wait(1)
            with pytest.raises(TimeoutError):
                other.result(timeout=0.02)
        finally:
            release.set()
        restart.result(timeout=2)
        other.result(timeout=2)
    assert events == ["write", "reload", "other-write"]


def test_guard_is_reentrant_and_timeout_never_enters_operation():
    guard = ReconciliationGuard()
    with guard.exclusive(timeout=0), guard.exclusive(timeout=0):
        pass
    @contextmanager
    def nested():
        with guard.exclusive(timeout=0):
            yield
    with ThreadPoolExecutor(max_workers=1) as pool, nested():
        def blocked():
            with guard.exclusive(timeout=0):
                pytest.fail("foreign owner entered mutation")
        with pytest.raises(ReconciliationBusy):
            pool.submit(blocked).result(timeout=1)
    with guard.exclusive(timeout=0):
        pass


@pytest.mark.parametrize("failure", ["busy", "unhealthy"])
def test_resync_failure_is_deferred_without_scanning_or_losing_durable_change(monkeypatch, failure):
    registry.init_db()
    engine, rec = Mock(), Mock(paused=False)
    if failure == "busy":
        engine.restart.side_effect = [ReconciliationBusy("busy"), None]
    else:
        engine.wait_healthy.side_effect = [False, True]
    recovery = AddressRecovery(engine, rec)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        media=engine, rec=rec, address_recovery=recovery)))
    scan = Mock(side_effect=AssertionError("deferred apply triggered network discovery"))
    monkeypatch.setattr("backend.app.services.address_recovery.find_addresses", scan)
    resync_services(request)
    assert recovery._pending_apply.is_set()
    rec.start.assert_not_called()
    recovery.tick()
    assert not recovery._pending_apply.is_set() and engine.restart.call_count == 2
    rec.start.assert_called_once()
    scan.assert_not_called()


def test_request_arriving_during_apply_is_preserved_for_next_tick():
    engine, rec = Mock(), Mock(paused=True)
    recovery = AddressRecovery(engine, rec)
    called = [False]
    def apply():
        if not called[0]:
            called[0] = True
            recovery.request_apply()
    engine.restart.side_effect = apply
    recovery.request_apply()
    recovery.tick()
    assert recovery._pending_apply.is_set()
    recovery.tick()
    assert not recovery._pending_apply.is_set() and engine.restart.call_count == 2
    rec.start.assert_not_called()


def test_recovery_waiting_for_hub_does_not_register_a_lease_that_deadlocks_drain(tmp_path):
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    attempted = threading.Event()
    coordinator_thread = threading.get_ident()
    class ObservedGuard(ReconciliationGuard):
        @contextmanager
        def exclusive(self, *, timeout=5.0):
            if threading.get_ident() != coordinator_thread:
                attempted.set()
            with super().exclusive(timeout=timeout):
                yield
    engine = go2rtc.Go2rtc(manage=False, config_path=tmp_path / "config.json")
    engine.reconciliation = ObservedGuard()
    engine.release_live_producers = Mock(return_value=True)
    viewers = ViewerGate()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=engine, viewer_sessions=viewers)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.reconciliation.exclusive():
            recovery = pool.submit(media_routes.media_recover, camera.camera_id, request)
            assert attempted.wait(1)
            viewers.suspend(camera.camera_id, timeout=0.05)
        with pytest.raises(media_routes.HTTPException) as caught:
            recovery.result(timeout=2)
    assert caught.value.status_code == 409
    engine.release_live_producers.assert_not_called()


def test_deferred_apply_cancelled_while_waiting_for_guard_never_restarts_hub(tmp_path):
    attempted = threading.Event()
    caller = threading.get_ident()
    class ObservedGuard(ReconciliationGuard):
        @contextmanager
        def exclusive(self, *, timeout=5.0):
            if threading.get_ident() != caller:
                attempted.set()
            with super().exclusive(timeout=timeout):
                yield
    engine = go2rtc.Go2rtc(manage=False, config_path=tmp_path / "hub.json")
    engine.reconciliation = ObservedGuard()
    engine.restart = Mock()
    recorder = Mock(paused=False)
    recovery = AddressRecovery(engine, recorder)
    recovery.request_apply()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.reconciliation.exclusive():
            pending = pool.submit(recovery.tick)
            assert attempted.wait(1)
            recovery.stop()
        pending.result(timeout=2)
    engine.restart.assert_not_called()
    recorder.start.assert_not_called()
    assert recovery._pending_apply.is_set()
