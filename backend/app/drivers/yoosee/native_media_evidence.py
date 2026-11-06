"""Capture exact-unit media evidence without opening a camera connection."""

from collections.abc import Callable
from dataclasses import dataclass, field

from ...db import p2p
from . import capability_profiles as profiles
from . import capability_rollout as rollout
from . import capability_snapshot_store as snapshots
from .p2p.ptz_authority import PtzGrant
from .p2p.ptz_protocol import DIRECTIONS


@dataclass(frozen=True)
class NativeMediaEvidence:
    entry: p2p.P2PEnrollment = field(repr=False)
    grants: tuple[PtzGrant, ...] = field(repr=False)
    valid: Callable[[], bool] = field(repr=False)


def capture(camera_id: str, *, archive: bool, now: Callable[[], float]) -> NativeMediaEvidence | None:
    selected = rollout.selected(camera_id)
    if selected is None or "native_video" not in selected[1]:
        return None
    identity = selected[0]
    entry = p2p.read_registered_enrollment(camera_id)
    if entry is None or entry.device_id != identity.device_id:
        return None

    def scope() -> tuple[PtzGrant, ...] | None:
        if rollout.selected(camera_id) != selected or p2p.read_registered_enrollment(camera_id) != entry:
            return None
        if not snapshots.matches_recorded_identity(camera_id=camera_id, identity=identity, now=now()):
            return None
        profile = profiles.load(camera_id=camera_id, identity=identity, now=now())
        proofs = [item for item in profile.operations if item.key == "native_video"] if profile else []
        if not (len(proofs) == 1 and proofs[0].readable is True and proofs[0].writable is False
                and "hevc" in proofs[0].options):
            return None
        if archive:
            av = [item for item in profile.operations if item.key == "native_av"] if profile else []
            if ("native_av" not in selected[1] or len(av) != 1 or av[0].readable is not True
                    or av[0].writable is not False
                    or not {"hevc", "aac_lc_16k_mono", "original_archive"} <= av[0].options):
                return None
        if "native_media_ptz" not in selected[1]:
            return ()
        motion = [item for item in profile.operations if item.key == "native_media_ptz"] if profile else []
        if (len(motion) != 1 or motion[0].writable is not True or motion[0].readable is not False
                or not motion[0].options or not motion[0].options <= DIRECTIONS.keys()):
            return None
        return (PtzGrant(entry.access_id, int(entry.device_id), motion[0].options),)

    grants = scope()
    if grants is None:
        return None
    return NativeMediaEvidence(entry, grants, lambda: scope() == grants)
