"""Dynamic resource proofs never grant a whole catalogue or another unit's writes."""
import base64
import struct
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_resources import (
    ResourceObservation,
    ResourceProfile,
    ResourceSelectionProof,
    certified_resources,
    resource_identity_digest,
)
from backend.app.drivers.yoosee.p2p.alarm_voice import AlarmVoiceResource

CAMERA = "cam_" + "1" * 24
IDENTITY = CapabilityIdentity("7000000002", "123", "model", 1, "firmware", "sdk", "")


def resource(number=7, opaque=10, system=True):
    native = base64.b64encode(struct.pack("<IIQQ", 4, number, 0, opaque)).decode()
    return AlarmVoiceResource(
        f"{'system' if system else 'custom'}-{number}", "Sound", 1000, "AMR",
        system, number, native,
    )


ITEM = resource()
OBSERVATION = ResourceObservation(
    CAMERA, IDENTITY, 100, 200, (ITEM, resource(8)), authenticated=True, complete=True,
)
PROFILE = ResourceProfile(CAMERA, IDENTITY, True, (
    ResourceSelectionProof(ITEM.key, resource_identity_digest(ITEM)),
))


def select(observation=OBSERVATION, profile=PROFILE, **kwargs):
    return certified_resources(observation, profile, **(
        {"camera_id": CAMERA, "identity": IDENTITY, "now": 150} | kwargs
    ))


def test_only_observed_and_individually_certified_resources_are_selectable():
    assert select() == (ITEM,)
    assert select(profile=None) == ()
    assert select(profile=replace(PROFILE, selections=())) == ()
    assert select(replace(OBSERVATION, resources=(resource(8),))) == ()


def test_reused_semantic_slot_does_not_inherit_prior_resource_certification():
    changed = resource(opaque=99)
    assert changed.key == ITEM.key
    assert resource_identity_digest(changed) != resource_identity_digest(ITEM)
    assert select(replace(OBSERVATION, resources=(changed,))) == ()
    assert select(replace(OBSERVATION, resources=(resource(system=False),))) == ()


def test_translated_label_does_not_change_native_resource_identity():
    translated = replace(ITEM, name="Zumbido")
    assert resource_identity_digest(translated) == resource_identity_digest(ITEM)
    assert select(replace(OBSERVATION, resources=(translated,))) == (translated,)


@pytest.mark.parametrize("field", ["device_id", "product_id", "model", "firmware", "sdk", "hardware"])
def test_profiles_and_observations_cannot_cross_identity_dimensions(field):
    other = replace(IDENTITY, **{field: "other"})
    assert select(identity=other) == ()
    assert select(replace(OBSERVATION, identity=other)) == ()
    assert select(profile=replace(PROFILE, identity=other)) == ()


def test_camera_and_revision_must_match():
    assert select(camera_id="cam_" + "2" * 24) == ()
    assert select(replace(OBSERVATION, camera_id="cam_" + "2" * 24)) == ()
    assert select(profile=replace(PROFILE, camera_id="cam_" + "2" * 24)) == ()
    assert select(identity=replace(IDENTITY, revision=2)) == ()


@pytest.mark.parametrize("now", [99, 200, True, float("nan"), float("inf")])
def test_stale_future_or_invalid_clocks_fail_closed(now):
    assert select(now=now) == ()


@pytest.mark.parametrize("changes", [
    {"collected_at": 0}, {"collected_at": True}, {"collected_at": 151},
    {"expires_at": float("nan")}, {"expires_at": 100000},
    {"resources": (ITEM, ITEM)}, {"resources": (ITEM,) * 201},
    {"authenticated": False}, {"authenticated": 1}, {"complete": False}, {"complete": 1},
])
def test_ambiguous_or_unbounded_observations_fail_closed(changes):
    assert select(replace(OBSERVATION, **changes)) == ()


@pytest.mark.parametrize("changes", [
    {"enumeration_verified": False}, {"enumeration_verified": 1},
    {"selections": PROFILE.selections * 2}, {"selections": PROFILE.selections * 201},
    {"selections": (ResourceSelectionProof(ITEM.key, "invalid digest"),)},
])
def test_enumeration_and_selection_proofs_are_independent(changes):
    assert select(profile=replace(PROFILE, **changes)) == ()
