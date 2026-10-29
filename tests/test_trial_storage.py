"""The operator storage experiment cannot stop unrelated camera recordings."""

from unittest.mock import Mock

import pytest

from backend.app.diagnostics.trial_storage import TrialStorage
from backend.app.recording.recorder import Recorder
from scripts import native_dashboard_trial as module


def test_isolated_policy_uses_real_archive_plan_without_starting_captures(monkeypatch):
    production=Recorder(segment_seconds=300)
    production.pause=Mock(side_effect=AssertionError("do not pause other cameras"))
    production.resume=Mock(side_effect=AssertionError("do not touch other cameras"))
    monkeypatch.setattr(Recorder,"start",lambda _: pytest.fail("no capture allowed"))
    trial=TrialStorage(production)
    plan=trial.recorder.native_archive("cam_"+"a"*24)
    assert plan.root==production.root and plan.segment_seconds==300
    assert plan.saving_allowed() and trial.advance(19) is None
    assert trial.advance(20)=="paused" and trial.paused and not plan.saving_allowed()
    assert trial.advance(40) is None
    assert trial.advance(50)=="resumed" and not trial.paused and plan.saving_allowed()
    assert trial.advance(200) is None
    assert not trial.recorder._procs and trial.recorder._maint is None
    production.pause.assert_not_called()
    production.resume.assert_not_called()


@pytest.mark.parametrize("extra", [[], ["--native-av"],
    ["--native-av","--driver-av-evidence","unused.md","--seconds","90"],
    ["--native-av","--driver-av-evidence","unused.md","--recover-at","20"],
    ["--native-av","--driver-av-evidence","unused.md","--ptz-direction","right"],
])
def test_invalid_storage_trials_are_refused_before_docker(monkeypatch,extra):
    monkeypatch.setattr(module,"TrialContainers",lambda: pytest.fail("no Docker operation allowed"))
    monkeypatch.setattr("sys.argv",["trial","--camera-id","cam_"+"a"*24,
        "--directory","unused","--confirm-interruption","--storage-pause",*extra])
    with pytest.raises(SystemExit): module.main()
