"""No hardware: broker exclusion is reentrant and bounded across threads."""
import threading

import pytest

from backend.app.drivers.yoosee.p2p import control_ownership as module


def test_reentrant_owner_excludes_another_thread_and_releases_on_failure(monkeypatch):
    monkeypatch.setattr(module, "WAIT_SECONDS", 0.01)
    observed = []
    def other():
        try:
            with module.own_control_channel():
                observed.append("entered")
        except module.ControlChannelBusy:
            observed.append("busy")
    with pytest.raises(ValueError):
        with module.own_control_channel(), module.own_control_channel():
            worker = threading.Thread(target=other)
            worker.start()
            worker.join(timeout=1)
            assert not worker.is_alive() and observed == ["busy"]
            assert not module.control_channel_contended()
            raise ValueError("test")
    worker = threading.Thread(target=other)
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive() and observed == ["busy", "entered"]


def test_competing_waiter_is_visible_without_releasing_owner(monkeypatch):
    monkeypatch.setattr(module, "WAIT_SECONDS", 2)
    waiting, entered = threading.Event(), threading.Event()
    original = module._channel
    owner_thread = threading.get_ident()
    class Lock:
        def acquire(self, **kwargs):
            if threading.get_ident() != owner_thread: waiting.set()
            return original.acquire(**kwargs)
        def release(self): original.release()
    monkeypatch.setattr(module, "_channel", Lock())
    def other():
        with module.own_control_channel(): entered.set()
    with module.own_control_channel():
        assert not module.control_channel_contended()
        with module.own_control_channel():
            assert not module.control_channel_contended()
        worker = threading.Thread(target=other)
        worker.start()
        assert waiting.wait(1)
        assert module.control_channel_contended() and not entered.is_set()
        # Observing contention never releases the channel behind the owner's back.
        assert not entered.is_set()
    worker.join(timeout=2)
    assert not worker.is_alive() and entered.is_set()
    assert not module.control_channel_contended()


def test_interrupted_acquisition_does_not_leave_phantom_demand(monkeypatch):
    class InterruptedLock:
        def acquire(self, **kwargs): raise KeyboardInterrupt
    monkeypatch.setattr(module, "_channel", InterruptedLock())
    with pytest.raises(KeyboardInterrupt):
        with module.own_control_channel(): pytest.fail("must not enter")
    assert not module.control_channel_contended()


def test_shared_worker_drains_stop_before_yielding_to_waiter(monkeypatch):
    from tests.test_account_read_worker import worker as make_worker
    from tests.test_account_worker_ptz import SPEC, MotionSocket

    monkeypatch.setattr(module, "WAIT_SECONDS", 2)
    original = module._channel
    owner_thread = threading.get_ident()
    waiting, entered = threading.Event(), threading.Event()
    class Lock:
        def acquire(self, **kwargs):
            if threading.get_ident() != owner_thread: waiting.set()
            return original.acquire(**kwargs)
        def release(self): original.release()
    monkeypatch.setattr(module, "_channel", Lock())
    sock = MotionSocket()
    owner = make_worker(sock)
    def other():
        with module.own_control_channel():
            assert sock.closed == 1 and len(sock.moves) == 2
            entered.set()
    contender = threading.Thread(target=other)
    def start_waiter():
        contender.start()
        assert waiting.wait(1)
    sock.on_start = start_waiter
    try:
        with module.own_control_channel():
            result = owner.run(sock, initial_motion=SPEC, wait=sock.wait,
                               cancelled=module.control_channel_contended)
            assert result.motion.release_delivery_confirmed and sock.closed == 1
            assert not entered.is_set()
    finally:
        if contender.ident is not None: contender.join(timeout=2)
    assert not contender.is_alive() and entered.is_set()
