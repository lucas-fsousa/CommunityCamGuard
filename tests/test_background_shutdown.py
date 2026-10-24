"""Every registered background service must reject a timed-out thread join."""

import threading
from unittest.mock import Mock

import pytest

from backend.app.media.source_registry import SourceCleanupUnconfirmed
from backend.app.recording.playback import Warmer
from backend.app.recording.recorder import Recorder
from backend.app.recording.retention import RetentionCleaner
from backend.app.recording.storage import StorageMonitor
from backend.app.services.address_recovery import AddressRecovery
from backend.app.services.control_sessions import ControlSessions
from backend.app.services.thread_shutdown import join_thread, require_retired


def test_a_thread_that_failed_to_start_is_resource_free_without_joining_or_running():
    run = Mock()
    thread = threading.Thread(target=run)
    join_thread(thread, timeout=0)
    require_retired(thread)
    run.assert_not_called()


@pytest.mark.parametrize("kind", ["controls", "recovery", "storage", "retention", "warmer", "recorder"])
def test_stop_requires_actual_background_thread_exit_without_skipping_recorder_cleanup(kind):
    services = {
        "controls": ControlSessions, "recovery": lambda: AddressRecovery(Mock(), Mock()),
        "storage": lambda: StorageMonitor(Mock()), "retention": RetentionCleaner,
        "warmer": Warmer, "recorder": Recorder,
    }
    service = services[kind]()
    thread = Mock()
    thread.is_alive.return_value = True
    if kind == "recorder":
        service._maint = thread
        service._index = Mock()
        service._terminate_all_locked = Mock()
    else:
        service._thread = thread
    with pytest.raises(SourceCleanupUnconfirmed):
        service.stop()
    assert service._stop.is_set() and thread.join.call_count == 1
    if kind == "recorder":
        service._terminate_all_locked.assert_called_once()  # still retire all children
    thread.is_alive.return_value = False
    service.stop()
    assert thread.join.call_count == 2
