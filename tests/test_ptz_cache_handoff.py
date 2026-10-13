import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.drivers.yoosee import native_ptz
from backend.app.drivers.yoosee.p2p import control_ownership
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from tests.test_native_av_broker_ownership import available_from_other_thread
from tests.test_ptz_cache import pool  # noqa: F401


@pytest.fixture(autouse=True)
def clean_gate(monkeypatch):
    monkeypatch.setattr(control_ownership, "_cleanup_uncertain", False)


def idle(cache, state, key=("camera",)):
    lease = cache.acquire(key, state.prepare)
    lease.confirmed = True
    lease.close()
    return lease.route


def test_handoff_closes_idle_before_yield_and_resumes(pool):  # noqa: F811
    cache, state = pool
    route = idle(cache, state)
    with cache.quiesce():
        assert route.closed == 1 and not cache._idle and cache._closing == 0
        with pytest.raises(P2PProbeError): cache.acquire(("other",), state.prepare)
        with pytest.raises(P2PProbeError): cache.warm(("other",), state.prepare)
        with pytest.raises(P2PProbeError):
            with cache.quiesce(): pytest.fail("nested handoff")
    cache.acquire(("new",), state.prepare).close()
    assert state.prepares == 2


def test_failed_close_retires_all_idle_but_never_yields(pool, monkeypatch):  # noqa: F811
    cache, state = pool
    first, second = idle(cache, state), idle(cache, state, ("other",))
    def fail(): raise OSError("private")
    monkeypatch.setattr(first, "close", fail)
    with pytest.raises(OSError):
        with cache.quiesce(): pytest.fail("unsafe auth")
    assert second.closed == 1 and cache._closing == 0
    with pytest.raises(P2PProbeError): cache.acquire(("new",), state.prepare)


def test_timer_removed_route_must_finish_close_before_handoff(pool, monkeypatch):  # noqa: F811
    cache, state = pool
    route = idle(cache, state)
    entered, release, yielded = threading.Event(), threading.Event(), threading.Event()
    def close():
        entered.set()
        assert release.wait(2)
        route.closed += 1
    monkeypatch.setattr(route, "close", close)
    state.now = 8
    timer = state.timers[0]
    def handoff():
        with cache.quiesce():
            assert route.closed == 1
            yielded.set()
    with ThreadPoolExecutor(max_workers=2) as workers:
        expiry = workers.submit(timer.callback, *timer.args)
        try:
            assert entered.wait(1)
            assert not cache._idle and cache._closing == 1
            transfer = workers.submit(handoff)
            assert not yielded.wait(0.05)
        finally:
            release.set()
        expiry.result(timeout=2)
        transfer.result(timeout=2)
    assert yielded.is_set()


def test_unconfirmed_timer_close_blocks_even_if_it_later_finishes(pool, monkeypatch):  # noqa: F811
    cache, state = pool
    route = idle(cache, state)
    entered, release = threading.Event(), threading.Event()
    def close():
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(route, "close", close)
    state.now = 8
    timer = state.timers[0]
    with ThreadPoolExecutor(max_workers=1) as workers:
        expiry = workers.submit(timer.callback, *timer.args)
        try:
            assert entered.wait(1)
            with pytest.raises(P2PProbeError, match="cleanup"):
                with cache.quiesce(): pytest.fail("unsafe auth")
        finally:
            release.set()
        expiry.result(timeout=2)
    with pytest.raises(P2PProbeError):
        with cache.quiesce(): pytest.fail("reset bypass")


def test_native_owner_holds_broker_until_handoff_exit(pool, monkeypatch):  # noqa: F811
    cache, state = pool
    idle(cache, state)
    monkeypatch.setattr(native_ptz, "_routes", cache)
    monkeypatch.setattr(native_ptz, "_active", {})
    monkeypatch.setattr(native_ptz, "_warming", {})
    with native_ptz.reserve_read_handoff():
        assert not available_from_other_thread()
        assert cache._paused
    assert available_from_other_thread() and not cache._paused


@pytest.mark.parametrize("busy", ["_active", "_warming"])
def test_native_owner_rejects_pending_ptz_without_closing_it(pool, monkeypatch, busy):  # noqa: F811
    cache, state = pool
    route = idle(cache, state)
    monkeypatch.setattr(native_ptz, "_routes", cache)
    monkeypatch.setattr(native_ptz, "_active", {})
    monkeypatch.setattr(native_ptz, "_warming", {})
    monkeypatch.setattr(native_ptz, busy, {"camera": object()})
    with pytest.raises(native_ptz.ControlNotReady):
        with native_ptz.reserve_read_handoff(): pytest.fail("unsafe auth")
    assert route.closed == 0 and available_from_other_thread()


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_handoff_failure_blocks_all_legacy_logins(pool, monkeypatch, error):  # noqa: F811
    cache, _ = pool
    monkeypatch.setattr(native_ptz, "_routes", cache)
    monkeypatch.setattr(native_ptz, "_active", {})
    monkeypatch.setattr(native_ptz, "_warming", {})
    with pytest.raises(error):
        with native_ptz.reserve_read_handoff():
            raise error("new owner cleanup uncertain")
    with pytest.raises(control_ownership.ControlChannelBusy, match="cleanup"):
        with control_ownership.own_control_channel():
            pytest.fail("legacy login after uncertain close")


def test_timer_close_failure_blocks_legacy_handoff(pool, monkeypatch):  # noqa: F811
    cache, state = pool
    route = idle(cache, state)
    def fail(): raise OSError("private")
    monkeypatch.setattr(route, "close", fail)
    state.now = 8
    timer = state.timers[0]
    with pytest.raises(OSError): timer.callback(*timer.args)
    monkeypatch.setattr(native_ptz, "_routes", cache)
    monkeypatch.setattr(native_ptz, "_active", {})
    monkeypatch.setattr(native_ptz, "_warming", {})
    with pytest.raises(P2PProbeError):
        with native_ptz.reserve_read_handoff(): pytest.fail("authentication")
    assert control_ownership._cleanup_uncertain
