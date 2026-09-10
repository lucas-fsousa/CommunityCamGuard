from dataclasses import replace

import pytest

from backend.app.drivers.yoosee import capability_profiles as profiles
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import OperationProof, ValidatedProfile

IDENTITY = CapabilityIdentity("7000000002", "123", "model", 1, "firmware", "sdk", "")
PROFILE = ValidatedProfile(
    "cam_" + "1" * 24,
    IDENTITY,
    (OperationProof("orientation", writable=True, options=frozenset({"normal", "inverted"})),),
)
SOURCES = {"orientation": "sha256:" + "a" * 64 + " proof.md#orientation"}


def load(**kwargs):
    return profiles.load(
        **({"camera_id": PROFILE.camera_id, "identity": IDENTITY, "now": 150} | kwargs)
    )


def test_roundtrip_and_exact_identity_binding():
    assert profiles.register(PROFILE, sources=SOURCES, reviewed_at=100)
    assert load() == PROFILE
    for field in ("device_id", "product_id", "model", "firmware", "sdk", "hardware"):
        assert load(identity=replace(IDENTITY, **{field: "other"})) is None
    assert load(identity=replace(IDENTITY, revision=2)) is None
    assert load(camera_id="cam_" + "2" * 24) is None


def test_old_review_cannot_restore_removed_operation_or_options():
    reduced = replace(
        PROFILE,
        operations=(OperationProof("orientation", writable=True, options=frozenset({"normal"})),),
    )
    assert profiles.register(reduced, sources=SOURCES, reviewed_at=120)
    assert not profiles.register(PROFILE, sources=SOURCES, reviewed_at=100)
    assert not profiles.register(PROFILE, sources=SOURCES, reviewed_at=120)
    assert load() == reduced


@pytest.mark.parametrize(
    "sources",
    [{}, {"orientation": "no digest"}, SOURCES | {"night_vision": SOURCES["orientation"]}],
)
def test_provenance_required_for_each_operation(sources):
    with pytest.raises(ValueError):
        profiles.register(PROFILE, sources=sources, reviewed_at=100)
    assert load() is None


def test_revision_and_future_review_are_unknown(monkeypatch):
    profiles.register(PROFILE, sources=SOURCES, reviewed_at=100)
    assert load(now=99) is None
    assert load(now=True) is None
    monkeypatch.setattr(profiles, "REVISION", profiles.REVISION + 1)
    assert load() is None
