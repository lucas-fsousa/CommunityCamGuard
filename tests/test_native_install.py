"""Installed native media keeps existing driver controls and persistent support."""

import json
from dataclasses import replace

import pytest

from backend.app.db import connect
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


@pytest.mark.parametrize('changed_field',[None,'firmware','sdk','model'])
def test_reinstall_preserves_stronger_transport_review_only_for_exact_identity(evidence,monkeypatch,changed_field):
    camera,identity,_,profile=evidence
    reference='sha256:'+'b'*64+' docs/proof#lan-udp'
    video=replace(profile.operations[0],options=frozenset({'hevc','lan_udp'}))
    assert capability_profiles.register(replace(profile,operations=(video,)),
        sources={'native_video':reference},reviewed_at=101)
    current=replace(identity,**{changed_field:'changed'}) if changed_field else identity
    monkeypatch.setattr(native_install.registry,'get_camera_by_id',lambda _:camera)
    monkeypatch.setattr(native_install,'collect_snapshot',lambda _:CapabilitySnapshot(camera.camera_id,current,150,()))
    monkeypatch.setattr(native_install,'time',lambda:150)
    installed_reference='sha256:'+'a'*64+' docs/native#installation'
    offered=native_install.prepare(camera.camera_id,Recorder().native_archive(camera.camera_id),installed_reference)
    assert offered is not None and offered.valid()
    installed=capability_profiles.load(camera_id=camera.camera_id,identity=current,now=151)
    native_video=next(op for op in installed.operations if op.key=='native_video')
    assert ('lan_udp' in native_video.options) is (changed_field is None)
    with connect() as conn:
        row=conn.execute('SELECT sources FROM yoosee_operation_profiles WHERE camera_id=?',(camera.camera_id,)).fetchone()
    assert json.loads(row['sources'])['native_video']==(installed_reference if changed_field else reference)
