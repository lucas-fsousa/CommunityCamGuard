import pytest

from backend.app.drivers.yoosee.capability_evidence import (
    EvidenceState,
    enum_property_evidence,
)


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        ({"t": 100, "setVal": {"nightViewMode": 0}}, EvidenceState.SUPPORTED),
        ({"t": 100.0, "setVal": {"nightViewMode": 2.0}}, EvidenceState.SUPPORTED),
        ({"t": -1, "setVal": {"nightViewMode": 0}}, EvidenceState.UNSUPPORTED),
        ({"t": 0, "setVal": {"nightViewMode": 0}}, EvidenceState.UNKNOWN),
        ({"t": 100, "setVal": {"nightViewMode": 20001}}, EvidenceState.UNKNOWN),
        ({"t": 100, "setVal": {"nightViewMode": False}}, EvidenceState.UNKNOWN),
        ({"t": True, "setVal": {"nightViewMode": 0}}, EvidenceState.UNKNOWN),
        ({"t": float("nan"), "setVal": {"nightViewMode": 0}}, EvidenceState.UNKNOWN),
        ({"t": 100, "setVal": {}}, EvidenceState.UNKNOWN),
        ({"setVal": {"nightViewMode": 0}}, EvidenceState.UNKNOWN),
        (None, EvidenceState.UNKNOWN),
    ],
)
def test_night_vision_evidence_distinguishes_off_absent_and_unknown(observation, expected):
    assert enum_property_evidence(
        observation, field="nightViewMode", supported_values=frozenset({0, 1, 2})
    ) == expected


def test_unsupported_sentinel_is_specific_to_the_feature():
    assert enum_property_evidence(
        {"t": 100, "setVal": {"cryDetectEn": 0}},
        field="cryDetectEn",
        supported_values=frozenset({1, 2}),
        unsupported_values=frozenset({0}),
    ) == EvidenceState.UNSUPPORTED
