"""Per-device controls on an existing account lifecycle; never owns authentication."""

from collections.abc import Callable

from ...db.p2p import P2PEnrollment
from ..contracts import WeeklySchedule
from .p2p.account_read_lifecycle import AccountReadLifecycle
from .p2p.alarm_voice import AlarmVoiceResource
from .p2p.broker_mailbox import MailboxTicket, ReadOutcome
from .p2p.catalog_read_inbox import CatalogReadTicket
from .p2p.catalog_read_job import CatalogReadResult, CatalogReadSpec
from .p2p.lamp_control_specs import LampWriteSpec
from .p2p.lamp_read_inbox import LampReadTicket
from .p2p.lamp_read_job import LampReadResult, LampReadSpec
from .p2p.model_control_inbox import ControlWriteTicket
from .p2p.model_control_job import (
    ControlWriteResult,
    NightVisionWriteSpec,
    OrientationWriteSpec,
    VolumeWriteSpec,
)
from .p2p.model_control_specs import AlarmVoiceWriteSpec, GuardScheduleWriteSpec, GuardWriteSpec
from .p2p.ptz_authority import PtzGrant
from .p2p.ptz_inbox import MotionTicket
from .p2p.ptz_scheduler import PtzSpec, ScheduledPtzResult
from .p2p.siren_pulse_inbox import SirenPulseTicket
from .p2p.siren_pulse_job import SirenPulseResult, SirenPulseSpec


class SharedDeviceControls:
    _camera_id: str
    _device_id: str
    _active_entry: P2PEnrollment | None
    _lifecycle: AccountReadLifecycle
    _motion_grants: tuple[PtzGrant, ...]
    _motion_valid: Callable[[], bool] | None

    def matches_target(self, camera_id: str, device_id: str) -> bool:
        return self._camera_id == camera_id and self._device_id == device_id

    def submit(self, path: str) -> MailboxTicket:
        """Only the fixed identity/state read scope; no caller device ID or write."""
        return self._lifecycle.submit(int(self._device_id), path)

    def take(self, ticket: MailboxTicket) -> ReadOutcome | None:
        return self._lifecycle.take(ticket)

    def cancel(self, ticket: MailboxTicket) -> bool:
        return self._lifecycle.cancel(ticket)

    def matches_enrollment(self, entry: P2PEnrollment) -> bool:
        return self._active_entry == entry and self._lifecycle.state == "active"

    def submit_catalog_read(self, system: bool) -> CatalogReadTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active resource catalogue enrollment required')
        return self._lifecycle.submit_catalog_read(CatalogReadSpec(entry.access_id, system))

    def take_catalog_read(self, ticket: CatalogReadTicket) -> CatalogReadResult | None:
        return self._lifecycle.take_catalog_read(ticket)

    def cancel_catalog_read(self, ticket: CatalogReadTicket) -> None:
        self._lifecycle.cancel_catalog_read(ticket)

    def submit_siren_pulse(self, seconds: int) -> SirenPulseTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active siren enrollment required')
        return self._lifecycle.submit_siren_pulse(SirenPulseSpec(entry.access_id, int(entry.device_id), seconds))

    def take_siren_pulse(self, ticket: SirenPulseTicket) -> SirenPulseResult | None:
        return self._lifecycle.take_siren_pulse(ticket)

    def cancel_siren_pulse(self, ticket: SirenPulseTicket) -> None:
        self._lifecycle.cancel_siren_pulse(ticket)

    def submit_lamp_read(self) -> LampReadTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError("active lamp-read enrollment required")
        return self._lifecycle.submit_lamp_read(LampReadSpec(entry.access_id, int(entry.device_id)))

    def submit_lamp_write(self, enabled: bool) -> LampReadTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active lamp-write enrollment required')
        return self._lifecycle.submit_lamp_write(LampWriteSpec(entry.access_id, int(entry.device_id), enabled))

    def take_lamp_write(self, ticket: LampReadTicket) -> LampReadResult | None:
        return self._lifecycle.take_lamp_read(ticket)

    def cancel_lamp_write(self, ticket: LampReadTicket) -> None:
        self._lifecycle.cancel_lamp_read(ticket)

    def submit_alarm_voice_write(self, resource: AlarmVoiceResource) -> ControlWriteTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active resource selection enrollment required')
        return self._lifecycle.submit_control_write(AlarmVoiceWriteSpec(int(entry.device_id), resource))

    def submit_volume_write(self, percent: int) -> ControlWriteTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active volume enrollment required')
        return self._lifecycle.submit_control_write(VolumeWriteSpec(int(entry.device_id), percent))

    def submit_orientation_write(self, orientation: str) -> ControlWriteTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active orientation enrollment required')
        return self._lifecycle.submit_control_write(OrientationWriteSpec(int(entry.device_id), orientation))

    def take_control_write(self, ticket: ControlWriteTicket) -> ControlWriteResult | None:
        return self._lifecycle.take_control_write(ticket)

    def submit_night_vision_write(self, mode: str) -> ControlWriteTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active night-vision enrollment required')
        return self._lifecycle.submit_control_write(NightVisionWriteSpec(int(entry.device_id), mode))

    def submit_guard_write(self, enabled: bool) -> ControlWriteTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active guard enrollment required')
        return self._lifecycle.submit_control_write(GuardWriteSpec(int(entry.device_id), enabled))

    def submit_guard_schedule_write(self, schedule: WeeklySchedule) -> ControlWriteTicket:
        entry = self._active_entry
        if entry is None:
            raise ValueError('active guard enrollment required')
        return self._lifecycle.submit_control_write(GuardScheduleWriteSpec(int(entry.device_id), schedule))

    def cancel_control_write(self, ticket: ControlWriteTicket) -> None:
        self._lifecycle.cancel_control_write(ticket)

    def take_lamp_read(self, ticket: LampReadTicket) -> LampReadResult | None:
        return self._lifecycle.take_lamp_read(ticket)

    def cancel_lamp_read(self, ticket: LampReadTicket) -> None:
        self._lifecycle.cancel_lamp_read(ticket)

    def submit_motion(self, direction: str) -> MotionTicket:
        entry = self._active_entry
        if entry is None or not self._motion_grants:
            raise ValueError("reviewed active media motion scope required")
        grant = self._motion_grants[0]
        if grant.access_id != entry.access_id:
            raise ValueError("media motion enrollment changed")
        # Local evidence only; never authenticate/refresh from an admission check.
        if self._motion_valid is not None and self._motion_valid() is not True:
            raise ValueError("media motion evidence is no longer current")
        return self._lifecycle.submit_motion(PtzSpec(grant.access_id, grant.device_id,
                                                    direction, grant.directions))

    def take_motion(self, ticket: MotionTicket) -> ScheduledPtzResult | None:
        return self._lifecycle.take_motion(ticket)

    def cancel_motion(self, ticket: MotionTicket) -> bool:
        return self._lifecycle.cancel_motion(ticket)

    def abandon_motion(self, ticket: MotionTicket) -> None:
        self._lifecycle.abandon_motion(ticket)

