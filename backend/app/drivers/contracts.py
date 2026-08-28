"""Vendor-neutral contracts for controls exposed by camera drivers.

Drivers may translate these semantic operations to ONVIF, a proprietary LAN protocol or a
vendor P2P transport.  The API never receives opcodes, thing-model paths or raw payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ControlValue = bool | int | str
ControlKind = Literal["boolean", "choice", "action", "number"]


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
