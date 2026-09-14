"""Bounded stopped-session reuse; no clocks sleeping or live sockets."""
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.p2p import ptz_cache


class Route:
    def __init__(self):
        self.closed = 0
        self.renewed = 0
    def close(self):
        self.closed += 1
    def renew(self, direction=None):
        self.renewed += 1
        self.renewed_direction = direction
        return Route()
    def send_start(self):
        pass
    def send_release(self):
        pass
    def confirm_release(self, **kwargs):
        return True


@pytest.fixture
def pool(monkeypatch):
    state = SimpleNamespace(now=0.0, timers=[], prepares=0)
    monkeypatch.setattr(ptz_cache.time, "monotonic", lambda: state.now)
    class Timer:
        def __init__(self, duration, callback, args):
            self.duration, self.callback, self.args = duration, callback, args
            state.timers.append(self)
        def start(self):
            pass
        def cancel(self):
            pass
    monkeypatch.setattr(ptz_cache.threading, "Timer", Timer)
    def prepare():
        state.prepares += 1
        return Route()
    state.prepare = prepare
    return ptz_cache.PtzRouteCache(), state


def test_only_confirmed_stopped_session_is_reused(pool):
    cache, state = pool
    lease = cache.acquire(("camera", "right", "credentials"), state.prepare)
    assert not lease.reused
    lease.send_start()
    lease.send_release()
    assert lease.confirm_release(deadline=1)
    lease.close()
    next_lease = cache.acquire(lease.key, state.prepare)
    assert next_lease.reused and state.prepares == 1
    next_lease.close()  # unused/cancelled session cannot enter the cache
    assert next_lease.route.closed == 1


def test_direction_change_reuses_only_stopped_session(pool):
    cache, state = pool
    first = cache.acquire(("camera", "reviewed-profile", "credentials"), state.prepare,
                          direction="right")
    first.confirmed = True
    first.close()
    second = cache.acquire(first.key, state.prepare, direction="up")
    assert second.reused and state.prepares == 1
    assert first.route.renewed_direction == "up"
    second.close()


@pytest.mark.parametrize("advance", [9, 21])
def test_idle_and_absolute_deadlines_are_enforced_even_if_timer_late(pool, advance):
    cache, state = pool
    first = cache.acquire(("camera",), state.prepare)
    first.confirmed = True
    first.close()
    state.now = advance
    second = cache.acquire(first.key, state.prepare)
    assert not second.reused and state.prepares == 2 and first.route.closed == 1
    second.close()


def test_changed_credentials_do_not_reuse_and_idle_timer_closes(pool):
    cache, state = pool
    first = cache.acquire(("camera", "old"), state.prepare)
    first.confirmed = True
    first.close()
    second = cache.acquire(("camera", "new"), state.prepare)
    assert not second.reused
    timer = state.timers[0]
    timer.callback(*timer.args)
    assert first.route.closed == 1
    second.close()


def test_failed_send_is_not_cached_even_with_later_confirmed_release(pool, monkeypatch):
    cache, state = pool
    lease = cache.acquire(("camera",), state.prepare)
    def fail():
        raise OSError("ambiguous")
    monkeypatch.setattr(lease.route, "send_start", fail)
    with pytest.raises(OSError):
        lease.send_start()
    lease.confirm_release(deadline=1)
    lease.close()
    assert lease.route.closed == 1 and not state.timers


def test_cache_capacity_is_four_idle_sockets(pool):
    cache, state = pool
    for index in range(5):
        lease = cache.acquire((index,), state.prepare)
        lease.confirmed = True
        lease.close()
    assert lease.route.closed == 1 and len(state.timers) == 5
    assert len(cache._idle) == 4


def test_absolute_age_is_not_extended_by_repeated_reuse(pool):
    cache, state = pool
    for now in (0, 6, 12, 18):
        state.now = now
        lease = cache.acquire(("camera",), state.prepare)
        lease.confirmed = True
        lease.close()
    assert state.prepares == 1
    assert state.timers[-1].duration == 2
    state.now = 20
    final = cache.acquire(("camera",), state.prepare)
    assert not final.reused and state.prepares == 2
    final.close()


def test_old_timer_cannot_close_renewed_socket_owner(pool):
    cache, state = pool
    first = cache.acquire(("camera",), state.prepare)
    first.confirmed = True
    first.close()
    second = cache.acquire(first.key, state.prepare)
    second.confirmed = True
    second.close()
    old = state.timers[0]
    old.callback(*old.args)
    assert second.route.closed == 0
    with pytest.raises(RuntimeError):
        second.send_release()
