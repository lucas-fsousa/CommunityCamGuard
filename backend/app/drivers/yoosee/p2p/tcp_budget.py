"""Reviewed finite experiment profiles, never browser-supplied timeout values."""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class TcpBudget:
    observation_seconds: int
    receive_seconds: int
    session_seconds: int
    broker_seconds: int
    route_seconds: int
    traffic_multiplier: int


class TcpProfile(Enum):
    SHORT = TcpBudget(3, 8, 15, 15, 20, 1)
    # Internal opt-in only. Does not relax handshake/idle/assembly bounds
    # or allocate larger retained buffers.
    EXTENDED = TcpBudget(10, 20, 22, 28, 48, 4)

    @property
    def budget(self) -> TcpBudget:
        return self.value


def validate_profile(profile: TcpProfile) -> None:
    if not isinstance(profile, TcpProfile):
        raise ValueError("unreviewed TCP experiment profile")
