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
            raise ValueError("test")
    worker = threading.Thread(target=other)
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive() and observed == ["busy", "entered"]
