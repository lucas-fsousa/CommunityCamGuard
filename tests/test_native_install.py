"""Installed native media keeps existing driver controls and persistent support."""

from backend.app.drivers.yoosee import capability_profiles, capability_rollout, native_install
from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot
from backend.app.recording.recorder import Recorder
from tests.test_native_media_offer import evidence as evidence


def test_install_merges_review_and_uses_normal_driver_offer(evidence,monkeypatch):
    camera,identity,_,_=evidence
    camera.capabilities['ptz']=True
    monkeypatch.setattr(native_install.registry,'get_camera_by_id',lambda _:camera)
    monkeypatch.setattr(native_install,'collect_snapshot',lambda _:CapabilitySnapshot(camera.camera_id,identity,150,()))
    monkeypatch.setattr(native_install,'time',lambda:150)
    recorder=Recorder()
    offered=native_install.prepare(camera.camera_id,recorder.native_archive(camera.camera_id),
                                  'sha256:'+'a'*64+' docs/native#installation')
    assert offered is not None and offered.valid()
    profile=capability_profiles.load(camera_id=camera.camera_id,identity=identity,now=151)
    assert {p.key for p in profile.operations}=={'native_video','native_av','native_media_ptz'}
    assert capability_rollout.selected(camera.camera_id)[0]==identity
    source=offered.factory().current
    assert source._thread is None and source._session._motion_grants[0].directions==frozenset({'up','down','left','right'})
    assert not recorder.root.exists()
