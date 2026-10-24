"""Admission/drain concurrency without camera or media process I/O."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketState

from backend.app import session_channels
from backend.app.api.viewer_sessions import run_viewer
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from backend.app.media.viewer_gate import ViewerGate

CAMERA = "cam_" + "a" * 24
OTHER = "cam_" + "b" * 24


def test_suspension_closes_admission_then_waits_for_registered_cleanup_only():
    gate = ViewerGate(capacity=2)
    first, other = gate.admit(CAMERA), gate.admit(OTHER)
    with ThreadPoolExecutor(max_workers=1) as pool:
        drain = pool.submit(gate.suspend, CAMERA, timeout=1)
        assert first.cancelled.wait(1)
        assert not other.cancelled.is_set()
        with pytest.raises(SourceUnavailable):
            gate.admit(CAMERA)
        with pytest.raises(SourceUnavailable):
            gate.suspend(CAMERA, timeout=0.01)  # cannot steal another in-flight drain
        with pytest.raises(TimeoutError):
            drain.result(timeout=0.02)
        gate.release(first)
        drain.result(timeout=1)
    assert gate.is_suspended(CAMERA)
    gate.resume(CAMERA)
    replacement = gate.admit(CAMERA)
    assert not replacement.cancelled.is_set()
    gate.release(first)  # repeated/foreign releases do not retire replacement
    with pytest.raises(SourceUnavailable):
        gate.admit(OTHER)
    gate.release(other)
    gate.release(replacement)


def test_failed_drain_stays_held_until_a_successful_retry():
    gate = ViewerGate()
    lease = gate.admit(CAMERA)
    with pytest.raises(SourceCleanupUnconfirmed):
        gate.suspend(CAMERA, timeout=0.01)
    gate.release(lease)
    with pytest.raises(SourceCleanupUnconfirmed):
        gate.resume(CAMERA)  # late cleanup alone is not the earlier timeout's receipt
    gate.suspend(CAMERA, timeout=0.1)
    gate.resume(CAMERA)


def test_hold_capacity_is_bounded_and_existing_holds_can_be_retried():
    gate = ViewerGate()
    for index in range(32):
        gate.suspend(f"cam_{index:024x}", timeout=0.1)
    with pytest.raises(SourceUnavailable):
        gate.suspend(CAMERA, timeout=0.1)
    gate.suspend("cam_" + "0" * 24, timeout=0.1)
    gate.resume("cam_" + "0" * 24)
    gate.suspend(CAMERA, timeout=0.1)


@pytest.mark.parametrize("timeout", [0, -1, 31, float("nan"), float("inf"), True])
def test_timeout_is_finite_bounded_and_does_not_mutate_admission(timeout):
    gate = ViewerGate()
    with pytest.raises(ValueError):
        gate.suspend(CAMERA, timeout=timeout)
    assert not gate.is_suspended(CAMERA)


def test_asgi_adapter_keeps_lease_until_operation_finally_has_finished(monkeypatch):
    gate = ViewerGate(capacity=1)
    entered, cleaning, finish = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(session_channels, "CHECK_INTERVAL", 0.005)

    class Socket:
        app = SimpleNamespace(state=SimpleNamespace(viewer_sessions=gate))
        application_state = WebSocketState.CONNECTED
        async def close(self, code=1000):
            self.application_state = WebSocketState.DISCONNECTED

    async def operation():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleaning.set()
            while not finish.is_set():
                await asyncio.sleep(0.002)

    def coordinate():
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            drain = pool.submit(gate.suspend, CAMERA, timeout=2)
            try:
                assert cleaning.wait(1)
                with pytest.raises(TimeoutError):
                    drain.result(timeout=0.02)
            finally:
                finish.set()
            drain.result(timeout=1)

    async def run():
        coordinator = asyncio.create_task(asyncio.to_thread(coordinate))
        await asyncio.wait_for(run_viewer(Socket(), CAMERA, "opaque", operation, lambda _: True), 3)
        await coordinator
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(run())
    gate.resume(CAMERA)
    gate.release(gate.admit(CAMERA))
