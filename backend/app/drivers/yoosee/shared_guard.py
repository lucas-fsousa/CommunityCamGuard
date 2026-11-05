"""Typed guard switch/weekly plan on the current native owner, never a second login."""

from ...db.p2p import P2PEnrollment
from ..contracts import ControlResult, WeeklySchedule
from .guard_plan import WEEKDAY_BITS
from .shared_control_write import apply


def write_enabled(entry: P2PEnrollment, enabled: bool) -> ControlResult | None:
    if type(enabled) is not bool:
        raise ValueError('smart-protection state must be a boolean')
    return apply(entry, 'smart_protection', enabled, None,
                 lambda session: session.submit_guard_write(enabled))


def write_schedule(entry: P2PEnrollment, schedule: WeeklySchedule) -> ControlResult | None:
    if not isinstance(schedule, WeeklySchedule):
        raise ValueError('smart-protection schedule must be a weekly schedule')
    # B7 returns the weekday bitmask in Sunday-first order. User order is not
    # scheduling semantics and must not cause false changes/readback failures.
    canonical = WeeklySchedule(schedule.start, schedule.end,
        tuple(day for day in WEEKDAY_BITS if day in schedule.weekdays))
    return apply(entry, 'smart_protection_schedule', canonical, None,
                 lambda session: session.submit_guard_schedule_write(canonical))
