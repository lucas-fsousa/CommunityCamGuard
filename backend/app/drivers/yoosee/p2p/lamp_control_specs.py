"""Closed lamp contracts: status read or exact boolean write, never generic JSON."""

from dataclasses import dataclass, field


def valid_target(access_id: int, device_id: int) -> bool:
    return (type(access_id) is int and 0 <= access_id < 1 << 64
            and type(device_id) is int and 0 < device_id < 1 << 64)


@dataclass(frozen=True, slots=True)
class LampReadSpec:
    access_id: int = field(repr=False)
    device_id: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class LampWriteSpec:
    access_id: int = field(repr=False)
    device_id: int = field(repr=False)
    enabled: bool

    def __post_init__(self):
        if not valid_target(self.access_id, self.device_id) or type(self.enabled) is not bool:
            raise ValueError('typed binary lamp write required')


@dataclass(frozen=True, slots=True)
class LampReadResult:
    status: str
    enabled: bool | None = None
    transport_acknowledged: bool = False
    error_code: int | None = None
    sent: bool = False  # Write offered to the sole owner, not an application acknowledgement.
