"""Speaker-volume evidence is independent of speaker/talkback operation proof."""
from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlDescriptor
from backend.app.drivers.yoosee.capability_evidence import EvidenceState as State
from backend.app.drivers.yoosee.capability_evidence import speaker_volume_evidence
from backend.app.drivers.yoosee.capability_policy import (
    OperationProof,
    ValidatedProfile,
    select_controls,
)
from tests.test_yoosee_capability_snapshot import batch, normalize


@pytest.mark.parametrize("raw", range(11))
def test_all_raw_buckets_including_mute_are_support_evidence(raw):
    assert speaker_volume_evidence({"t": 100, "setVal": raw}) == State.SUPPORTED


@pytest.mark.parametrize("raw", [True, False, -1, 11, 7.0, "7", None, {"value": 7}, [7]])
def test_invalid_or_nested_values_never_enable_volume(raw):
    assert speaker_volume_evidence({"t": 100, "setVal": raw}) == State.UNKNOWN


@pytest.mark.parametrize("timestamp,expected", [
    (-1, State.UNSUPPORTED), (0, State.UNKNOWN), (True, State.UNKNOWN),
    (None, State.UNKNOWN), (2**31, State.UNKNOWN), (float("nan"), State.UNKNOWN),
    (100, State.SUPPORTED),
])
def test_timestamp_domains(timestamp, expected):
    assert speaker_volume_evidence({"t": timestamp, "setVal": 7}) == expected


def test_snapshot_requires_successful_exact_volume_root():
    reads = batch()
    snapshot = normalize(reads)
    volume = next(item for item in snapshot.evidence if item.feature == "speaker_volume")
    assert volume.state == State.SUPPORTED
    assert volume.property_timestamp == 102
    for entries in (reads[:4], (*reads[:4], replace(reads[4], error_code=20001))):
        states = {item.feature: item.state for item in normalize(entries).evidence}
        assert states["speaker_volume"] == State.UNKNOWN
        assert states["night_vision"] == State.SUPPORTED
        assert "two_way_audio" not in states


def test_missing_property_does_not_search_adjacent_fields():
    for value in (None, 7, {"t": 100, "nested": {"setVal": 7}}):
        assert speaker_volume_evidence(value) == State.UNKNOWN


def test_volume_observation_never_grants_unproven_write_options():
    snapshot = normalize(batch())
    descriptor = ControlDescriptor(
        "speaker_volume", "choice", readable=True, writable=True,
        options=("0", "25", "50", "75", "100"),
    )
    profile = ValidatedProfile(snapshot.camera_id, snapshot.identity, (
        OperationProof("speaker_volume", readable=True, writable=True,
                       options=frozenset({"50", "75", "100"})),
    ))
    kwargs = dict(camera_id=snapshot.camera_id, identity=snapshot.identity, profile=profile)
    selected = select_controls((descriptor,), **kwargs,
                               evidence={"speaker_volume": State.SUPPORTED})
    assert selected[0].options == ("50", "75", "100")
    for evidence in ({}, {"speaker_volume": State.UNKNOWN},
                     {"speaker_volume": State.UNSUPPORTED}):
        assert select_controls((descriptor,), **kwargs, evidence=evidence) == ()
