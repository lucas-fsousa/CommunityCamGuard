from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlDescriptor
from backend.app.drivers.yoosee.capability_evidence import EvidenceState as State
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import (
    OperationProof,
    ValidatedProfile,
    select_controls,
)

IDENTITY = CapabilityIdentity("7000000002", "123", "model", 1, "firmware", "sdk", "")
CAMERA = "cam_" + "1" * 24
DESCRIPTOR = ControlDescriptor(
    "night_vision", "choice", readable=True, writable=True, options=("auto", "on", "off")
)
PROFILE = ValidatedProfile(
    CAMERA,
    IDENTITY,
    (OperationProof("night_vision", readable=True, writable=True, options=frozenset({"auto"})),),
)


def select(**kwargs):
    return select_controls(
        (DESCRIPTOR,),
        **(
            {
                "camera_id": CAMERA,
                "identity": IDENTITY,
                "profile": PROFILE,
                "evidence": {"night_vision": State.SUPPORTED},
            }
            | kwargs
        ),
    )


@pytest.mark.parametrize(
    "evidence", [{}, {"night_vision": State.UNKNOWN}, {"night_vision": State.UNSUPPORTED}]
)
def test_proof_alone_never_enables_feature(evidence):
    assert select(evidence=evidence) == ()


def test_evidence_alone_never_certifies_an_operation():
    assert select(profile=None) == ()
    assert select(profile=replace(PROFILE, operations=())) == ()


def test_profiles_do_not_cross_units_or_identity_dimensions():
    assert select(camera_id="cam_" + "2" * 24) == ()
    for field in ("device_id", "product_id", "model", "firmware", "sdk", "hardware"):
        assert select(identity=replace(IDENTITY, **{field: "other"})) == ()
    assert select(identity=replace(IDENTITY, revision=2)) == ()


def test_only_homologated_options_survive_and_inputs_are_unchanged():
    assert select()[0].options == ("auto",)
    assert DESCRIPTOR.options == ("auto", "on", "off")


def test_read_proof_does_not_enable_write_and_duplicate_proofs_fail_closed():
    profile = replace(PROFILE, operations=(OperationProof("night_vision", readable=True),))
    assert select(profile=profile)[0].readable
    assert not select(profile=profile)[0].writable
    assert select(profile=replace(PROFILE, operations=PROFILE.operations * 2)) == ()


def test_catalog_preview_uses_persisted_evidence_and_current_enrollment(monkeypatch):
    from types import SimpleNamespace

    from backend.app.drivers.yoosee import capability_snapshot_store as store
    from backend.app.drivers.yoosee import controls
    from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot, PropertyEvidence

    camera = SimpleNamespace(camera_id=CAMERA, capabilities={"night_vision": True})
    enrollment = SimpleNamespace(device_id=IDENTITY.device_id)
    monkeypatch.setattr(controls.p2p, "get_enrollment_for_camera", lambda _id: enrollment)
    option = next(iter(controls.NIGHT_VISION_VALUES))
    profile = replace(
        PROFILE,
        operations=(OperationProof("night_vision", writable=True, options=frozenset({option})),),
    )

    def preview(now=150):
        return controls.validated_catalog(camera, identity=IDENTITY, profile=profile, now=now)

    assert preview() == ()
    snapshot = CapabilitySnapshot(
        CAMERA, IDENTITY, 100, (PropertyEvidence("night_vision", State.SUPPORTED, 1),)
    )
    assert store.save(snapshot, generation=store.begin(CAMERA), expires_at=200)
    assert tuple(item.key for item in preview()) == ("night_vision",)
    assert preview()[0].options == (option,)
    from backend.app.drivers.yoosee import capability_profiles

    assert controls.stored_catalog(camera, identity=IDENTITY, now=150) == ()
    capability_profiles.register(
        profile,
        sources={"night_vision": "sha256:" + "a" * 64 + " test.md#night"},
        reviewed_at=100,
    )
    assert controls.stored_catalog(camera, identity=IDENTITY, now=150) == preview()
    assert preview(now=200) == ()
    enrollment.device_id = "different"
    assert preview() == ()


def test_static_profile_cannot_enable_dynamic_resource_enumeration():
    descriptor = ControlDescriptor("night_vision", "choice", writable=True, dynamic_options=True)
    assert (
        select_controls(
            (descriptor,),
            camera_id=CAMERA,
            identity=IDENTITY,
            profile=PROFILE,
            evidence={"night_vision": State.SUPPORTED},
        )
        == ()
    )
