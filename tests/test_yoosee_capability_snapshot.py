from dataclasses import replace

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee import capability_collector as collector
from backend.app.drivers.yoosee.capability_evidence import EvidenceState as State
from backend.app.drivers.yoosee.capability_snapshot import normalize_snapshot
from backend.app.drivers.yoosee.p2p.contracts import P2PPropertyRead

DEVICE = "7000000002"
CAMERA = "cam_" + "1" * 24


def batch():
    values = (
        {"productID": "6442451494", "productModel": "model", "revision": 1},
        {"swVer": "40.1.22", "sdkVer": "16.20.16355", "hwVer": ""},
        {"t": 100, "setVal": {"nightViewMode": 0}},
        {"t": 101, "setVal": {"cryDetectEn": 0}},
    )
    return tuple(
        P2PPropertyRead(DEVICE, path, True, False, False, 0, value)
        for path, value in zip(collector.CAPABILITY_PATHS, values, strict=True)
    )


def normalize(reads, **kwargs):
    return normalize_snapshot(
        reads, **({"camera_id": CAMERA, "device_id": DEVICE, "collected_at": 2000} | kwargs)
    )


def test_old_property_timestamp_is_not_server_receipt_time_or_unsupported():
    snapshot = normalize(batch())
    assert snapshot.collected_at == 2000
    assert snapshot.evidence[0].state == State.SUPPORTED
    assert snapshot.evidence[0].property_timestamp == 100
    assert snapshot.evidence[1].state == State.UNSUPPORTED
    assert not hasattr(snapshot, "value")


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (True, State.UNKNOWN),
        (0, State.UNKNOWN),
        (-1, State.UNSUPPORTED),
        (-2, State.UNKNOWN),
        (float("nan"), State.UNKNOWN),
        (2**40, State.UNKNOWN),
        (100.0, State.SUPPORTED),
    ],
)
def test_timestamp_domains(timestamp, expected):
    reads = list(batch())
    reads[2] = replace(reads[2], value={"t": timestamp, "setVal": {"nightViewMode": 0}})
    assert normalize(tuple(reads)).evidence[0].state == expected


def test_missing_failed_and_changed_feature_reads_do_not_reuse_previous_support():
    assert all(item.state == State.UNKNOWN for item in normalize(batch()[:2]).evidence)
    reads = list(batch())
    for code in (None, False, 20001):
        reads[2] = replace(batch()[2], error_code=code)
        assert normalize(tuple(reads)).evidence[0].state == State.UNKNOWN


def test_rejects_mixed_ambiguous_or_incomplete_identity_batches():
    reads = batch()
    assert normalize(reads[1:]) is None
    assert normalize((*reads[:2], reads[0])) is None
    for changes in (
        {"device_id": "7000000003"},
        {"authenticated": False},
        {"property_path": "ProWritable.unknown"},
    ):
        assert normalize((*reads[:3], replace(reads[3], **changes))) is None


@pytest.mark.parametrize("clock", [True, float("inf"), float("nan"), 0, -1])
def test_invalid_server_clock_is_not_accepted(clock):
    assert normalize(batch(), collected_at=clock) is None


@pytest.mark.parametrize(
    "field,value,feature,expected",
    [
        ("multiFlip", 1, "orientation", State.SUPPORTED),
        ("multiFlip", 3, "orientation", State.SUPPORTED),
        ("multiFlip", -1, "orientation", State.UNSUPPORTED),
        ("multiFlip", 0, "orientation", State.UNKNOWN),
        ("multiFlip", 2, "orientation", State.UNKNOWN),
        ("multiFlip", True, "orientation", State.UNKNOWN),
        ("enable", 0, "smart_protection", State.SUPPORTED),
        ("enable", 1, "smart_protection", State.SUPPORTED),
        ("enable", 20001, "smart_protection", State.UNKNOWN),
        ("enable", False, "smart_protection", State.UNKNOWN),
    ],
)
def test_homologated_control_fields_do_not_infer_related_features(field, value, feature, expected):
    reads = list(batch())
    index = 2 if field == "multiFlip" else 3
    reads[index] = replace(reads[index], value={"t": 100, "setVal": {field: value}})
    states = {item.feature: item.state for item in normalize(tuple(reads)).evidence}
    assert states[feature] == expected
    assert "smart_protection_schedule" not in states
    assert "siren_pulse" not in states


def test_collector_wrapper_samples_backend_time_after_collection(monkeypatch):
    calls = []

    def collect(enrollment):
        calls.append(enrollment)
        return batch()

    def now():
        assert len(calls) == 1
        return 3000

    monkeypatch.setattr(collector, "collect", collect)
    monkeypatch.setattr(collector.time, "time", now)
    enrollment = P2PEnrollment(DEVICE, 1, bytes(64), None, "", "", CAMERA)
    snapshot = collector.collect_snapshot(enrollment)
    assert snapshot == normalize(batch(), collected_at=3000)
