"""Immutable driver-reviewed PTZ scope; never derive this from browser input."""

from dataclasses import dataclass, field

from .ptz_protocol import DIRECTIONS
from .ptz_scheduler import PtzSpec


@dataclass(frozen=True, slots=True)
class PtzGrant:
    access_id: int = field(repr=False)
    device_id: int = field(repr=False)
    directions: frozenset[str]


class PtzAuthority:
    """Fixed per-account grants established by the driver after enrollment checks.

    This enforces scope, not vendor authentication or capability detection. No
    mutation/refresh or callback executes under the lifecycle admission lock.
    """

    def __init__(self, grants: tuple[PtzGrant, ...]):
        if not isinstance(grants, tuple) or len(grants) > 8:
            raise ValueError("bounded driver motion grants required")
        self._grants: dict[int, PtzGrant] = {}
        access: int | None = None
        for grant in grants:
            if (not isinstance(grant, PtzGrant)
                    or type(grant.access_id) is not int or not 0 < grant.access_id < 1 << 64
                    or type(grant.device_id) is not int or not 0 < grant.device_id < 1 << 64
                    or not isinstance(grant.directions, frozenset) or not grant.directions
                    or not grant.directions <= DIRECTIONS.keys()
                    or grant.device_id in self._grants
                    or (access is not None and access != grant.access_id)):
                raise ValueError("invalid or conflicting driver motion grant")
            access = grant.access_id
            self._grants[grant.device_id] = grant

    @property
    def enabled(self) -> bool:
        return bool(self._grants)

    def authorize(self, spec: PtzSpec) -> None:
        if (not isinstance(spec, PtzSpec) or type(spec.device_id) is not int
                or type(spec.access_id) is not int or not isinstance(spec.direction, str)):
            raise ValueError("typed gesture required")
        grant = self._grants.get(spec.device_id)
        if (grant is None or spec.access_id != grant.access_id
                or not isinstance(spec.reviewed_directions, frozenset)
                or not spec.reviewed_directions <= grant.directions
                or spec.direction not in grant.directions):
            raise ValueError("gesture outside driver-reviewed enrollment scope")
