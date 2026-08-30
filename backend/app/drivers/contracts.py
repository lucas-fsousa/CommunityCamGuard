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
_OPTION_VALUE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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
class AudioMessageResult:
    """Vendor-neutral result for one bounded server-to-camera voice message."""

    duration_ms: int
    requested_frames: int
    sent_frames: int
    acknowledged_frames: int
    direct_connection: bool
    session_completed: bool
    route_released: bool

    def __post_init__(self) -> None:
        if not 20 <= self.duration_ms <= 10_000 or self.duration_ms % 20:
            raise ValueError("audio-message duration must be complete 20 ms frames")
        if not (0 <= self.acknowledged_frames <= self.sent_frames <= self.requested_frames <= 500):
            raise ValueError("audio-message frame counts are inconsistent")

    @property
    def completed(self) -> bool:
        return (
            self.duration_ms > 0
            and self.requested_frames > 0
            and self.sent_frames == self.requested_frames
            and self.acknowledged_frames == self.requested_frames
            and self.direct_connection
            and self.session_completed
            and self.route_released
        )

    def public(self) -> dict[str, object]:
        return {
            "duration_ms": self.duration_ms,
            "requested_frames": self.requested_frames,
            "sent_frames": self.sent_frames,
            "acknowledged_frames": self.acknowledged_frames,
            "direct_connection": self.direct_connection,
            "session_completed": self.session_completed,
            "route_released": self.route_released,
            "completed": self.completed,
        }


@dataclass(frozen=True, slots=True)
class ControlDescriptor:
    """Safe public description of one semantic camera control."""

    key: str
    kind: ControlKind
    readable: bool = False
    writable: bool = False
    options: tuple[str, ...] = ()
    dynamic_options: bool = False

    def __post_init__(self) -> None:
        if self.dynamic_options and (self.kind != "choice" or self.options):
            raise ValueError("dynamic control options require a choice without static options")

    def public(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "readable": self.readable,
            "writable": self.writable,
            "options": list(self.options),
            "dynamic_options": self.dynamic_options,
        }


@dataclass(frozen=True, slots=True)
class ControlOption:
    """One safe runtime option returned by a camera-family driver."""

    value: str
    label: str
    group: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if _OPTION_VALUE.fullmatch(self.value) is None:
            raise ValueError("control option value is invalid")
        if not self.label.strip() or len(self.label) > 120:
            raise ValueError("control option label is invalid")
        if self.group is not None and _OPTION_VALUE.fullmatch(self.group) is None:
            raise ValueError("control option group is invalid")
        if self.detail is not None and len(self.detail) > 120:
            raise ValueError("control option detail is too long")

    def public(self) -> dict[str, object]:
        return {
            "value": self.value,
            "label": self.label,
            "group": self.group,
            "detail": self.detail,
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
