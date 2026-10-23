"""Managed-parent teardown receipts with fake processes; no services are started."""

import subprocess
from unittest.mock import Mock

import pytest

from backend.app.media.go2rtc import Go2rtc


@pytest.mark.parametrize("forced", [False, True])
def test_stop_reaps_parent_before_releasing_handle(forced):
    engine = Go2rtc(manage=True)
    process = Mock()
    if forced:
        process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 5), -9]
    engine._proc = process
    engine.stop()
    process.terminate.assert_called_once()
    assert process.wait.call_count == (2 if forced else 1)
    assert process.kill.call_count == int(forced)
    assert engine._proc is None


@pytest.mark.parametrize("failure", ["kill", "wait"])
def test_uncertain_parent_shutdown_retains_handle_and_aborts_restart(failure):
    engine = Go2rtc(manage=True)
    process = Mock()
    process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 5)
    if failure == "kill":
        process.kill.side_effect = OSError("synthetic failure")
    engine._proc = process
    engine.start = Mock(side_effect=AssertionError("replacement before confirmed exit"))
    with pytest.raises((OSError, subprocess.TimeoutExpired)):
        engine.restart([])
    assert engine._proc is process
    engine.start.assert_not_called()


def test_stop_without_owned_process_never_contacts_external_service():
    engine = Go2rtc(manage=False)
    engine.stop()
    assert engine._proc is None
