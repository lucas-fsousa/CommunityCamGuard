"""Shared result contract for bounded Yoosee intercom operations."""

from __future__ import annotations

from dataclasses import dataclass

from .intercom_session import IntercomControlResult


@dataclass(frozen=True, slots=True)
class IntercomProbeResult:
    device_id: str
    direct_handshake: bool
    media_meter_acknowledged: bool
    av_accepted: bool
    stream_version: int | None
    control: IntercomControlResult
    route_released: bool

    @property
    def completed(self) -> bool:
        return (
            self.direct_handshake
            and self.media_meter_acknowledged
            and self.av_accepted
            and self.control.completed
            and self.route_released
        )


def empty_intercom_result(device_id: str) -> IntercomProbeResult:
    return IntercomProbeResult(
        device_id,
        False,
        False,
        False,
        None,
        IntercomControlResult(False, False, False, False, False),
        False,
    )
