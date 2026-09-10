"""Pure APK guard-plan codec shared by capability evidence and control reads."""

from __future__ import annotations

from ..contracts import Weekday, WeeklySchedule

WEEKDAY_BITS: dict[Weekday, int] = {
    "sun": 1 << 0,
    "mon": 1 << 1,
    "tue": 1 << 2,
    "wed": 1 << 3,
    "thu": 1 << 4,
    "fri": 1 << 5,
    "sat": 1 << 6,
}


def _clock(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    hour, minute = value.get("hour"), value.get("min")
    if type(hour) is not int or type(minute) is not int:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_guard_plan(value: object) -> WeeklySchedule | None:
    """Parse exactly a plan, not parent/child alternatives or device timestamps.

    Preserve wall-clock times. Equal endpoints mean next-day, not an empty plan.
    The APK rejects an empty weekday mask; no timezone conversion belongs here.
    """
    if not isinstance(value, dict):
        return None
    start, end = _clock(value.get("start")), _clock(value.get("end"))
    mask = value.get("weekdayEn")
    if start is None or end is None or type(mask) is not int or not 1 <= mask <= 0x7F:
        return None
    return WeeklySchedule(start, end, tuple(day for day, bit in WEEKDAY_BITS.items() if mask & bit))
