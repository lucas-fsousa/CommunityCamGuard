"""Operator admission of an installed unit, preserving its existing driver catalogue."""

import json
from contextlib import closing
from time import time

from ...db import connect, p2p, registry
from ...media.archive_spec import NativeArchiveSpec
from . import capability_profiles as profiles
from . import capability_rollout as rollout
from . import capability_snapshot_store as snapshots
from .capability_collector import collect_snapshot
from .capability_policy import OperationProof, ValidatedProfile
from .native_media_offer import offer
from .p2p.ptz_protocol import DIRECTIONS


def prepare(camera_id: str, archive: NativeArchiveSpec, reference: str):
    camera=registry.get_camera_by_id(camera_id)
    entry=p2p.read_registered_enrollment(camera_id)
    if camera is None or entry is None or entry.camera_id!=camera_id:
        raise ValueError('registered camera required')
    snapshot=collect_snapshot(entry)
    if snapshot is None or snapshot.identity.device_id!=entry.device_id:
        raise RuntimeError('camera identity unavailable')
    identity=snapshot.identity
    previous=profiles.load(camera_id=camera_id,identity=identity,now=time())
    proofs={op.key:op for op in previous.operations} if previous else {}
    sources={}
    if previous:
        with closing(connect()) as conn:
            row=conn.execute('SELECT sources FROM yoosee_operation_profiles WHERE camera_id=?',(camera_id,)).fetchone()
            sources=json.loads(row['sources'])
    additions=[OperationProof('native_video',readable=True,options=frozenset({'hevc'})),
        OperationProof('native_av',readable=True,options=frozenset({'hevc','aac_lc_16k_mono','original_archive'}))]
    if camera.capabilities.get('ptz') is True:
        additions.append(OperationProof('native_media_ptz',writable=True,options=frozenset(DIRECTIONS)))
    for proof in additions:
        proofs[proof.key]=proof
        sources[proof.key]=reference
    profile=ValidatedProfile(camera_id,identity,tuple(proofs.values()))
    selected=rollout.selected(camera_id)
    resources=(frozenset({'alarm_voice'} & selected[1]) if selected and selected[0]==identity else frozenset())
    generation=snapshots.begin(camera_id)
    if not snapshots.save(snapshot,generation=generation,expires_at=snapshot.collected_at+1800):
        raise RuntimeError('identity changed during installation')
    if not profiles.register(profile,sources=sources,reviewed_at=time()):
        raise RuntimeError('driver catalogue changed during installation')
    rollout.activate(profile,resource_controls=resources)
    result=offer(camera,archive=archive)
    if result is None: raise RuntimeError('driver declined native media')
    return result
