"""Normal driver admission of original-codec A/V archives, without live camera IO."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from backend.app.drivers.base import CameraDriver
from backend.app.drivers.yoosee import capability_profiles, capability_rollout, native_media_offer
from backend.app.drivers.yoosee.capability_policy import OperationProof
from backend.app.drivers.yoosee.driver import YooseeDriver
from backend.app.drivers.yoosee.p2p.av_delivery import NativeAvDelivery
from backend.app.media.archive_spec import NativeArchiveSpec
from backend.app.media.source_registry import SourceUnavailable
from tests.test_native_media_offer import evidence as evidence

AV = OperationProof("native_av",readable=True,
                    options=frozenset({"hevc","aac_lc_16k_mono","original_archive"}))


def review(evidence, proof=AV, *, activate=True):
    profile = evidence[3]
    combined = replace(profile,operations=(*profile.operations,proof))
    capability_profiles.register(combined,sources={
        op.key:"sha256:" + "c"*64 + " proof#native-av" for op in combined.operations
    },reviewed_at=101)
    if activate: capability_rollout.activate(combined)


def test_archive_request_is_refused_without_av_proof_not_silently_video_only(evidence,tmp_path):
    camera=evidence[0]; spec=NativeArchiveSpec(camera.camera_id,tmp_path,300)
    assert YooseeDriver().native_media_offer(camera) is not None
    assert YooseeDriver().native_media_offer(camera,archive=spec) is None
    assert CameraDriver().native_media_offer(camera,archive=spec) is None


@pytest.mark.parametrize("proof", [
    replace(AV,readable=False,writable=True),replace(AV,writable=True),
    replace(AV,options=frozenset({"hevc"})),
    replace(AV,options=frozenset({"hevc","aac_lc_16k_mono"})),
])
def test_partial_or_writable_av_proof_does_not_authorize_recording(evidence,tmp_path,proof):
    review(evidence,proof)
    spec=NativeArchiveSpec(evidence[0].camera_id,tmp_path,300)
    assert YooseeDriver().native_media_offer(evidence[0],archive=spec) is None


def test_av_proof_needs_explicit_driver_rollout(evidence,tmp_path):
    review(evidence,activate=False)
    spec=NativeArchiveSpec(evidence[0].camera_id,tmp_path,300)
    assert YooseeDriver().native_media_offer(evidence[0],archive=spec) is None


def test_factory_passes_server_policy_to_native_source_without_mkdir_or_capture(evidence,tmp_path):
    review(evidence)
    camera=evidence[0]; spec=NativeArchiveSpec(camera.camera_id,tmp_path/"not-created",300)
    offer=YooseeDriver().native_media_offer(camera,archive=spec)
    assert offer is not None and offer.valid()
    recovered=offer.factory()
    source=recovered.current
    assert isinstance(source._delivery,NativeAvDelivery)
    assert source._archive==spec.template and source._archive_segment_seconds==300
    assert source._thread is None and not spec.root.exists()


def test_factory_retains_live_server_storage_policy(evidence,tmp_path):
    review(evidence)
    allowed=[True]
    def policy(): return allowed[0]
    spec=NativeArchiveSpec(evidence[0].camera_id,tmp_path,300,saving_allowed=policy)
    offered=YooseeDriver().native_media_offer(evidence[0],archive=spec)
    source=offered.factory().current
    assert source._saving_allowed is policy and source._saving_allowed()
    allowed[0]=False
    assert not source._saving_allowed() and offered.valid()


def test_revoked_av_proof_blocks_original_factory_before_construction(evidence,tmp_path,monkeypatch):
    review(evidence)
    camera=evidence[0]; spec=NativeArchiveSpec(camera.camera_id,tmp_path,300)
    offer=YooseeDriver().native_media_offer(camera,archive=spec)
    assert offer is not None
    capability_rollout.activate(evidence[3])
    factory=Mock(side_effect=AssertionError("source must not be constructed"))
    monkeypatch.setattr(native_media_offer,"NativeVideoSource",factory)
    assert not offer.valid()
    with pytest.raises(SourceUnavailable): offer.factory()
    factory.assert_not_called()


def test_other_camera_archive_cannot_be_attached_to_offer(evidence,tmp_path):
    spec=NativeArchiveSpec("cam_" + "f"*24,tmp_path,300)
    with pytest.raises(ValueError,match="selected camera"):
        YooseeDriver().native_media_offer(evidence[0],archive=spec)


def test_archive_offer_keeps_combined_motion_scope_and_revocation(evidence,tmp_path,monkeypatch):
    camera,_identity,entry,profile=evidence
    motion=OperationProof("native_media_ptz",writable=True,options=frozenset({"left","right"}))
    combined=replace(profile,operations=(*profile.operations,AV,motion))
    capability_profiles.register(combined,sources={
        op.key:"sha256:"+"d"*64+" proof#av-motion" for op in combined.operations
    },reviewed_at=102)
    capability_rollout.activate(combined)
    offered=YooseeDriver().native_media_offer(camera,archive=NativeArchiveSpec(camera.camera_id,tmp_path,300))
    assert offered is not None
    source=offered.factory().current
    session=source._session
    session._active_entry=entry
    assert session._motion_grants[0].directions==frozenset({"left","right"})
    submit=Mock(return_value="synthetic-ticket")
    monkeypatch.setattr(session._lifecycle,"submit_motion",submit)
    assert session.submit_motion("right")=="synthetic-ticket"
    capability_rollout.activate(replace(combined,operations=(profile.operations[0],motion)))
    assert not offered.valid()
    with pytest.raises(ValueError,match="evidence"):
        session.submit_motion("right")
    assert submit.call_count==1 and source._archive_segment_seconds==300
