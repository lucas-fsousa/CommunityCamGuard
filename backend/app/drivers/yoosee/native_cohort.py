"""Fixed reviewed cohort shared by every source in one driver plan."""

from .native_media_evidence import NativeMediaEvidence
from .p2p.ptz_authority import PtzAuthority


class NativeCohort:
    def __init__(self, evidence: tuple[NativeMediaEvidence, ...]):
        entries = tuple(item.entry for item in evidence)
        if (not entries or any(not item.camera_id for item in entries)
                or len({item.camera_id for item in entries}) != len(entries)
                or len({item.device_id for item in entries}) != len(entries)
                or len({item.access_id for item in entries}) != 1):
            raise ValueError("bounded reviewed single-account cohort required")
        self.entries = entries
        self.grants = tuple(grant for item in evidence for grant in item.grants)
        PtzAuthority(self.grants)
        self._checks = {item.entry.camera_id: item.valid for item in evidence}

    def valid(self, camera_id: str) -> bool:
        check = self._checks.get(camera_id)
        return check is not None and check() is True

    def current(self) -> bool:
        return all(check() is True for check in self._checks.values())
