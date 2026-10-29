"""Real driver/profile boundary with fake correlated reads and an isolated database."""

from pathlib import Path

import pytest

from backend.app.diagnostics import trial_driver_offer as module
from backend.app.diagnostics.trial_sources import prepare_source
from backend.app.drivers.yoosee import capability_rollout
from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot
from backend.app.drivers.yoosee.driver import YooseeDriver
from backend.app.media.archive_spec import NativeArchiveSpec
from backend.app.recording.recorder import Recorder
from tests.test_native_media_offer import evidence as evidence

DOCUMENT = Path(__file__).parents[1] / "docs/internal/native-av-delivery.md"


@pytest.fixture
def prepared(evidence,monkeypatch):
    camera,identity,enrollment,_ = evidence
    calls=[]
    def collect(entry):
        assert entry==enrollment
        calls.append(entry.camera_id)
        return CapabilitySnapshot(camera.camera_id,identity,150,())
    monkeypatch.setattr(module,"collect_snapshot",collect)
    monkeypatch.setattr(module,"time",lambda: 150)
    monkeypatch.setattr(module.drivers,"for_camera",lambda _: YooseeDriver())
    return camera,enrollment,calls


def test_review_builds_normal_driver_archive_offer_without_motion_or_files(prepared,tmp_path):
    camera,entry,calls=prepared
    spec=NativeArchiveSpec(camera.camera_id,tmp_path/"not-created",300)
    offer=module.prepare_driver_offer(camera.camera_id,entry,spec,DOCUMENT)
    assert offer.valid() and calls==[camera.camera_id]
    assert capability_rollout.selected(camera.camera_id)[1]==frozenset({"native_video","native_av"})
    source=offer.factory().current
    assert source._archive==spec.template and source._archive_segment_seconds==300
    assert not source._session._motion_grants and source._thread is None
    assert not spec.root.exists()


def test_normal_operator_plan_uses_driver_and_actual_recording_policy(prepared):
    camera,entry,_=prepared
    recorder=Recorder(segment_seconds=300)
    planned=prepare_source(camera.camera_id,entry,recorder,sample_audio=False,native_av=True,
                           ptz_directions=frozenset(),recover=True,driver_evidence=DOCUMENT)
    assert planned.offer.valid() and planned.audio is None and planned.restarts==0
    assert planned.current._archive_segment_seconds==300
    assert str(planned.current._archive)==recorder._output_template(camera.camera_id)
    assert planned.frame_rate is None and planned.current._thread is None


def test_missing_snapshot_refuses_offer_instead_of_constructing_capture(prepared,tmp_path,monkeypatch):
    camera,entry,_=prepared
    monkeypatch.setattr(module,"collect_snapshot",lambda _: None)
    with pytest.raises(RuntimeError,match="identity"):
        module.prepare_driver_offer(camera.camera_id,entry,NativeArchiveSpec(camera.camera_id,tmp_path,300),DOCUMENT)


def test_cross_camera_review_fails_before_collection(prepared,tmp_path):
    camera,entry,calls=prepared
    other=NativeArchiveSpec("cam_"+"f"*24,tmp_path,300)
    with pytest.raises(ValueError): module.prepare_driver_offer(camera.camera_id,entry,other,DOCUMENT)
    assert calls==[]


def test_evidence_outside_repository_documentation_is_not_read(tmp_path):
    path=tmp_path/"review.md"
    path.write_text("not a repository review")
    with pytest.raises(ValueError): module.evidence_reference(path)


@pytest.mark.parametrize("native_av,sample_audio,directions", [
    (False,False,frozenset()),(True,True,frozenset()),(True,False,frozenset({"right"})),
])
def test_reviewed_driver_trial_does_not_enable_unscoped_paths(prepared,native_av,sample_audio,directions):
    camera,entry,calls=prepared
    with pytest.raises(ValueError):
        prepare_source(camera.camera_id,entry,Recorder(),sample_audio=sample_audio,native_av=native_av,
                       ptz_directions=directions,recover=False,driver_evidence=DOCUMENT)
    assert calls==[]
