"""Vendor-neutral contracts for controls exposed by camera drivers.

Drivers may translate these semantic operations to ONVIF, a proprietary LAN protocol or a
vendor P2P transport.  The API never receives opcodes, thing-model paths or raw payloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Weekday = Literal["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
_WEEKDAYS: tuple[Weekday, ...] = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_CLOCK = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


@dataclass(frozen=True, slots=True)
class WeeklySchedule:
    """Vendor-neutral recurring local-time window used by camera automation controls."""

    start: str
    end: str
    weekdays: tuple[Weekday, ...]

    def __post_init__(self) -> None:
        if not _CLOCK.fullmatch(self.start) or not _CLOCK.fullmatch(self.end):
            raise ValueError("weekly-schedule times must use 24-hour HH:MM")
        if not self.weekdays or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekly schedule must contain unique active weekdays")
        if any(day not in _WEEKDAYS for day in self.weekdays):
            raise ValueError("weekly schedule contains an unknown weekday")

    def public(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "weekdays": list(self.weekdays)}


ControlValue = bool | int | str | WeeklySchedule
ControlKind = Literal["boolean", "choice", "action", "number", "weekly_schedule"]


class ControlNotReady(RuntimeError):
    """The driver supports a control, but this camera lacks required runtime material."""


class ControlOperationError(RuntimeError):
    """A typed driver operation failed after the camera and control were resolved."""


@dataclass(frozen=True, slots=True)
class ControlDescriptor:
    """Safe public description of one semantic camera control."""

    key: str
    kind: ControlKind
    readable: bool = False
    writable: bool = False
    options: tuple[str, ...] = ()

    def public(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "readable": self.readable,
            "writable": self.writable,
            "options": list(self.options),
        }


@dataclass(frozen=True, slots=True)
class ControlResult:
    """Transport-neutral result returned by a driver control operation."""

    key: str
    value: ControlValue
    previous_value: ControlValue | None = None
    changed: bool | None = None
    verified: bool = False
    authenticated: bool = False
    direct_connection: bool = False
    transport_acknowledged: bool = False
    application_acknowledged: bool = False
    error_code: int | None = None
    native_previous_value: int | None = None
    native_requested_value: int | None = None


def public_control_value(value: ControlValue | None) -> object:
    """Project a domain control value into JSON-compatible semantic data."""

    return value.public() if isinstance(value, WeeklySchedule) else value
