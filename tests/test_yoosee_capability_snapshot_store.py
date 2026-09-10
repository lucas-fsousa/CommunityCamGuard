from dataclasses import replace

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee import capability_refresh as refresh
from backend.app.drivers.yoosee import capability_snapshot_store as store
from backend.app.drivers.yoosee.capability_evidence import EvidenceState as State
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot, PropertyEvidence

CAMERA = "cam_" + "1" * 24
IDENTITY = CapabilityIdentity("7000000002", "6442451494", "model", 1, "40.1.22", "sdk", "")
SNAPSHOT = CapabilitySnapshot(
    CAMERA,
    IDENTITY,
    100,
    (
        PropertyEvidence("night_vision", State.SUPPORTED, 1),
        PropertyEvidence("cry_detection", State.UNSUPPORTED, 2),
    ),
)


def resolve(**kwargs):
    return store.resolve(
        **(
            {"camera_id": CAMERA, "identity": IDENTITY, "feature": "night_vision", "now": 150}
            | kwargs
        )
    )


def test_newer_started_job_wins_even_if_older_finishes_later():
    old = store.begin(CAMERA)
    new = store.begin(CAMERA)
    assert store.save(SNAPSHOT, generation=new, expires_at=200)
    assert not store.save(replace(SNAPSHOT, collected_at=160), generation=old, expires_at=220)
    assert resolve() == State.SUPPORTED
    assert resolve(feature="cry_detection") == State.UNSUPPORTED
    assert not store.save(SNAPSHOT, generation=new, expires_at=999)
    assert resolve(now=200) == State.UNKNOWN


def test_start_invalidates_all_old_evidence_and_partial_new_snapshot_does_not_merge():
    assert store.save(SNAPSHOT, generation=store.begin(CAMERA), expires_at=200)
    ticket = store.begin(CAMERA)
    assert resolve() == State.UNKNOWN
    assert resolve(feature="cry_detection") == State.UNKNOWN
    assert store.save(replace(SNAPSHOT, evidence=()), generation=ticket, expires_at=200)
    assert resolve() == State.UNKNOWN


def test_every_identity_dimension_and_camera_is_part_of_lookup():
    assert store.save(SNAPSHOT, generation=store.begin(CAMERA), expires_at=200)
    for field in ("device_id", "product_id", "model", "firmware", "sdk", "hardware"):
        assert resolve(identity=replace(IDENTITY, **{field: "different"})) == State.UNKNOWN
    assert resolve(identity=replace(IDENTITY, revision=2)) == State.UNKNOWN
    assert resolve(camera_id="cam_" + "2" * 24) == State.UNKNOWN


@pytest.mark.parametrize("now", [99, 200, 201, True, float("nan"), float("inf")])
def test_receipt_and_expiry_are_server_clock_boundaries(now):
    assert store.save(SNAPSHOT, generation=store.begin(CAMERA), expires_at=200)
    assert resolve(now=now) == State.UNKNOWN


def test_invalid_batch_is_not_partially_published_and_rules_invalidate(monkeypatch):
    ticket = store.begin(CAMERA)
    invalid = replace(SNAPSHOT, evidence=SNAPSHOT.evidence * 2)
    with pytest.raises(ValueError):
        store.save(invalid, generation=ticket, expires_at=200)
    assert resolve() == State.UNKNOWN
    assert store.save(SNAPSHOT, generation=ticket, expires_at=200)
    monkeypatch.setattr(store, "RULE_REVISION", store.RULE_REVISION + 1)
    assert resolve() == State.UNKNOWN


def test_refresh_reserves_before_io_and_failure_leaves_unknown(monkeypatch):
    enrollment = P2PEnrollment(IDENTITY.device_id, 1, bytes(64), None, "", "", CAMERA)

    def collect(_enrollment):
        assert resolve() == State.UNKNOWN
        return SNAPSHOT

    monkeypatch.setattr(refresh, "collect_snapshot", collect)
    assert refresh.refresh(enrollment, validity_seconds=100)
    assert resolve() == State.SUPPORTED

    def failure(_enrollment):
        assert resolve() == State.UNKNOWN
        raise OSError("offline")

    monkeypatch.setattr(refresh, "collect_snapshot", failure)
    with pytest.raises(OSError):
        refresh.refresh(enrollment, validity_seconds=100)
    assert resolve() == State.UNKNOWN
