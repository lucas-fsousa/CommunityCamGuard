"""Closed driver-owned write schemas; no raw path, arbitrary object or action opcode."""

from dataclasses import dataclass

from ...contracts import WeeklySchedule
from .alarm_voice import AlarmVoiceResource
from .night_vision import NIGHT_VISION_VALUES
from .orientation import ORIENTATION_VALUES
from .volume import VOLUME_RAW_BY_PERCENT


def _device(value: int) -> bool:
    return type(value) is int and 0 < value < 1 << 64


@dataclass(frozen=True, slots=True)
class VolumeWriteSpec:
    device_id: int
    percent: int

    def __post_init__(self):
        if not _device(self.device_id) or type(self.percent) is not int or self.percent not in VOLUME_RAW_BY_PERCENT:
            raise ValueError('typed camera volume required')


@dataclass(frozen=True, slots=True)
class OrientationWriteSpec:
    device_id: int
    orientation: str

    def __post_init__(self):
        if not _device(self.device_id) or type(self.orientation) is not str or self.orientation not in ORIENTATION_VALUES:
            raise ValueError('typed camera orientation required')


@dataclass(frozen=True, slots=True)
class NightVisionWriteSpec:
    device_id: int
    mode: str

    def __post_init__(self):
        if not _device(self.device_id) or type(self.mode) is not str or self.mode not in NIGHT_VISION_VALUES:
            raise ValueError('typed camera night-vision mode required')


@dataclass(frozen=True, slots=True)
class GuardWriteSpec:
    device_id: int
    enabled: bool

    def __post_init__(self):
        if not _device(self.device_id) or type(self.enabled) is not bool:
            raise ValueError('typed camera guard state required')


@dataclass(frozen=True, slots=True)
class GuardScheduleWriteSpec:
    device_id: int
    schedule: WeeklySchedule

    def __post_init__(self):
        if (not _device(self.device_id) or not isinstance(self.schedule, WeeklySchedule)
                or type(self.schedule.weekdays) is not tuple):
            raise ValueError('typed immutable camera guard schedule required')


@dataclass(frozen=True, slots=True)
class AlarmVoiceWriteSpec:
    device_id: int
    resource: AlarmVoiceResource

    def __post_init__(self):
        if not _device(self.device_id) or not isinstance(self.resource, AlarmVoiceResource):
            raise ValueError('typed catalogue resource selection required')


ControlWriteSpec = VolumeWriteSpec | OrientationWriteSpec | NightVisionWriteSpec | GuardWriteSpec | GuardScheduleWriteSpec | AlarmVoiceWriteSpec
CONTROL_WRITE_TYPES = (VolumeWriteSpec, OrientationWriteSpec, NightVisionWriteSpec, GuardWriteSpec, GuardScheduleWriteSpec, AlarmVoiceWriteSpec)
