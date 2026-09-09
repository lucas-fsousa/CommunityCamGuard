import pytest

from backend.app.drivers.yoosee import capability_store as store
from backend.app.drivers.yoosee.capability_evidence import EvidenceState

IDENTITY = dict(
    camera_id="cam_" + "1" * 24,
    device_id="1234567890",
    product="test-product",
    firmware="test-version",
    feature="night_vision",
)


def remember(state=EvidenceState.SUPPORTED, at=100):
    store.remember(**IDENTITY, state=state, observed_at=at, expires_at=200)


def test_persists_without_cross_camera_or_feature_support():
    remember()
    assert store.resolve(**IDENTITY, now=150) == EvidenceState.SUPPORTED
    for field, value in (
        ("camera_id", "cam_" + "2" * 24),
        ("device_id", "9999999999"),
        ("product", "other"),
        ("firmware", "next"),
        ("feature", "siren_pulse"),
    ):
        assert store.resolve(**(IDENTITY | {field: value}), now=150) == EvidenceState.UNKNOWN


@pytest.mark.parametrize("now", (99, 200, 201, float("nan"), True))
def test_expired_future_or_invalid_time_is_unknown(now):
    remember()
    assert store.resolve(**IDENTITY, now=now) == EvidenceState.UNKNOWN


def test_rule_revision_invalidates_previous_evidence(monkeypatch):
    remember()
    monkeypatch.setattr(store, "RULE_REVISION", 2)
    assert store.resolve(**IDENTITY, now=150) == EvidenceState.UNKNOWN


def test_late_response_does_not_overwrite_newer_negative_evidence():
    remember(EvidenceState.UNSUPPORTED, 120)
    remember(EvidenceState.SUPPORTED, 100)
    assert store.resolve(**IDENTITY, now=150) == EvidenceState.UNSUPPORTED


def test_unknown_read_does_not_become_supported():
    remember()
    remember(EvidenceState.UNKNOWN, 120)
    assert store.resolve(**IDENTITY, now=150) == EvidenceState.UNKNOWN


def test_missing_record_is_unknown():
    assert store.resolve(**IDENTITY, now=150) == EvidenceState.UNKNOWN
