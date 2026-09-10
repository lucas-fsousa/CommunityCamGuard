from copy import deepcopy

import pytest

from backend.app.drivers.yoosee.capability_evidence import EvidenceState as State
from backend.app.drivers.yoosee.capability_evidence import guard_schedule_evidence
from backend.app.drivers.yoosee.guard_plan import parse_guard_plan

PLAN = {"start": {"hour": 0, "min": 0}, "end": {"hour": 0, "min": 0}, "weekdayEn": 127}


def test_disabled_master_does_not_hide_supported_schedule_or_convert_wall_clock():
    assert (
        guard_schedule_evidence({"t": 100, "setVal": {"enable": 0, "plan": PLAN}})
        == State.SUPPORTED
    )
    parsed = parse_guard_plan(PLAN)
    assert parsed.start == parsed.end == "00:00"
    assert parsed.weekdays == ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    overnight = parse_guard_plan(
        PLAN | {"start": {"hour": 22, "min": 30}, "end": {"hour": 6, "min": 15}, "weekdayEn": 65}
    )
    assert (overnight.start, overnight.end, overnight.weekdays) == (
        "22:30",
        "06:15",
        ("sun", "sat"),
    )


@pytest.mark.parametrize("mask", [0, 128, 255, -1, True, 1.0, "127", None])
def test_invalid_weekday_mask_is_unknown(mask):
    assert (
        guard_schedule_evidence({"t": 100, "setVal": {"plan": PLAN | {"weekdayEn": mask}}})
        == State.UNKNOWN
    )


@pytest.mark.parametrize(
    "field,value",
    [("hour", 24), ("hour", -1), ("min", 60), ("min", True), ("hour", 1.0), ("min", "0")],
)
def test_invalid_time_components_are_not_capability_evidence(field, value):
    plan = deepcopy(PLAN)
    plan["end"][field] = value
    assert guard_schedule_evidence({"t": 100, "setVal": {"plan": plan}}) == State.UNKNOWN


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (0, State.UNKNOWN),
        (-1, State.UNSUPPORTED),
        (True, State.UNKNOWN),
        (2**40, State.UNKNOWN),
        (100.0, State.SUPPORTED),
    ],
)
def test_timestamp_semantics(timestamp, expected):
    assert guard_schedule_evidence({"t": timestamp, "setVal": {"plan": PLAN}}) == expected


def test_no_fallback_to_sibling_parent_or_child_payload():
    assert (
        guard_schedule_evidence({"t": 100, "setVal": {"enable": 1}, "plan": PLAN}) == State.UNKNOWN
    )
    assert guard_schedule_evidence({"t": 100, "setVal": {"plan": {"plan": PLAN}}}) == State.UNKNOWN
    assert (
        guard_schedule_evidence({"guardParm": {"t": 100, "setVal": {"plan": PLAN}}})
        == State.UNKNOWN
    )
