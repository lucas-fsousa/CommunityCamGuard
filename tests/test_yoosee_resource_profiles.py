import json
from dataclasses import replace

import pytest

from backend.app.db import connect
from backend.app.drivers.yoosee import capability_resource_profiles as store
from backend.app.drivers.yoosee.capability_resources import (
    ResourceSelectionProof,
    certified_resources,
)
from tests.test_yoosee_capability_resources import CAMERA, IDENTITY, OBSERVATION, PROFILE

REFERENCE = "sha256:" + "a" * 64 + " test-proof.md#selection"
SOURCES = {"enumeration": REFERENCE, PROFILE.selections[0].key: REFERENCE}


def register(profile=PROFILE, **kwargs):
    return store.register(profile, **(
        {"sources": SOURCES, "reviewed_at": 100, "expires_at": 200} | kwargs
    ))


def load(**kwargs):
    return store.load(**({"camera_id": CAMERA, "identity": IDENTITY, "now": 150} | kwargs))


def test_roundtrip_keeps_exact_identity_and_requires_fresh_observation():
    assert register()
    assert load() == PROFILE
    assert certified_resources(OBSERVATION, load(), camera_id=CAMERA, identity=IDENTITY,
                               now=150) == OBSERVATION.resources[:1]
    assert certified_resources(replace(OBSERVATION, complete=False), load(),
                               camera_id=CAMERA, identity=IDENTITY, now=150) == ()
    for field in ("device_id", "product_id", "model", "firmware", "sdk", "hardware"):
        assert load(identity=replace(IDENTITY, **{field: "other"})) is None
    assert load(identity=replace(IDENTITY, revision=2)) is None
    assert load(camera_id="cam_" + "2" * 24) is None


@pytest.mark.parametrize("now", [99, 200, 201, True, float("nan"), float("inf")])
def test_future_expired_invalid_time_cannot_load(now):
    register()
    assert load(now=now) is None


def test_revocation_prevents_older_review_resurrection_even_after_expiry():
    assert register()
    revoked = replace(PROFILE, enumeration_verified=False, selections=())
    assert register(revoked, sources={"enumeration": REFERENCE}, reviewed_at=120, expires_at=160)
    assert not register(reviewed_at=110, expires_at=300)
    assert not register(reviewed_at=120, expires_at=300)
    assert load() == revoked
    assert load(now=170) is None
    assert not register(reviewed_at=110, expires_at=300)
    assert load(now=170) is None


def test_newer_reduced_profile_replaces_instead_of_merging_and_revision_invalidates(monkeypatch):
    register()
    reduced = replace(PROFILE, selections=())
    assert register(reduced, sources={"enumeration": REFERENCE}, reviewed_at=120)
    assert load() == reduced
    monkeypatch.setattr(store, "REVISION", store.REVISION + 1)
    assert load() is None


@pytest.mark.parametrize("changes", [
    {"sources": {}}, {"sources": {"enumeration": REFERENCE}},
    {"sources": SOURCES | {"custom-9": REFERENCE}},
    {"reviewed_at": True}, {"reviewed_at": 0}, {"reviewed_at": float("nan")},
    {"expires_at": 100}, {"expires_at": float("inf")},
])
def test_invalid_provenance_and_validity_are_rejected_before_write(changes):
    with pytest.raises(ValueError):
        register(**changes)
    assert load() is None


@pytest.mark.parametrize("changes", [
    {"enumeration_verified": 1}, {"enumeration_verified": False},
    {"selections": PROFILE.selections * 2}, {"selections": PROFILE.selections * 201},
    {"selections": (ResourceSelectionProof("system-7", "bad"),)},
    {"selections": (ResourceSelectionProof("native/path", "a" * 64),)},
])
def test_invalid_or_ambiguous_profile_is_not_stored(changes):
    with pytest.raises(ValueError):
        register(replace(PROFILE, **changes))
    assert load() is None


@pytest.mark.parametrize("payload", [
    "not JSON", "null", "[]", '"string"',
    json.dumps({"enumeration_verified": 1, "selections": []}),
    json.dumps({"enumeration_verified": True, "selections": {}}),
    json.dumps({"enumeration_verified": True, "selections": [None]}),
    " " * 65537,
    "[" * 1500 + "]" * 1500,
])
def test_corrupted_or_oversized_stored_json_fails_closed(payload):
    register()
    with connect() as conn:
        conn.execute("UPDATE yoosee_alarm_resource_profiles SET profile=?", (payload,))
    assert load() is None


def test_corrupted_provenance_cannot_restore_permission():
    register()
    with connect() as conn:
        conn.execute("UPDATE yoosee_alarm_resource_profiles SET sources='{}'")
    assert load() is None
