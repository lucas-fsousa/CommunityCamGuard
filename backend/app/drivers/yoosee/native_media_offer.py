"""Exact-unit reviewed native-video offers, never an enrollment/brand-only grant."""

from time import time

from ...db import p2p
from ...db.registry import Camera
from ...media.source_offer import NativeSourceOffer
from ...media.source_registry import SourceUnavailable
from . import capability_profiles as profiles
from . import capability_rollout as rollout
from . import capability_snapshot_store as snapshots
from .native_recovery import wrap
from .native_video_source import NativeVideoSource
from .p2p.ptz_authority import PtzGrant
from .p2p.ptz_protocol import DIRECTIONS


def offer(camera: Camera) -> NativeSourceOffer | None:
    camera_id = camera.camera_id
    selected = rollout.selected(camera_id)
    if selected is None or "native_video" not in selected[1]:
        return None
    identity = selected[0]
    enrollment = p2p.read_registered_enrollment(camera_id)
    if enrollment is None or enrollment.device_id != identity.device_id:
        return None

    def scope() -> tuple[PtzGrant, ...] | None:
        current = rollout.selected(camera_id)
        if current != selected or p2p.read_registered_enrollment(camera_id) != enrollment:
            return None
        now = time()
        if not snapshots.is_current(camera_id=camera_id, identity=identity, now=now):
            return None
        profile = profiles.load(camera_id=camera_id, identity=identity, now=now)
        proofs = [item for item in profile.operations if item.key == "native_video"] if profile else []
        if not (len(proofs) == 1 and proofs[0].readable is True and proofs[0].writable is False
                and "hevc" in proofs[0].options):
            return None
        # Separate combined-session proof: enrollment or ordinary PTZ support does
        # not establish motor safety while the native media reader is active.
        if "native_media_ptz" not in selected[1]:
            return ()
        motion = [item for item in profile.operations if item.key == "native_media_ptz"] if profile else []
        if (len(motion) != 1 or motion[0].writable is not True or motion[0].readable is not False
                or not motion[0].options or not motion[0].options <= DIRECTIONS.keys()):
            return None
        return (PtzGrant(enrollment.access_id, int(enrollment.device_id), motion[0].options),)

    captured = scope()
    if captured is None:
        return None

    def valid() -> bool:
        return scope() == captured

    def construct():
        if not valid():
            raise SourceUnavailable("native media evidence changed")
        if captured:
            return NativeVideoSource(camera_id, enrollment.device_id, motion_grants=captured,
                                     motion_valid=valid)
        return NativeVideoSource(camera_id, enrollment.device_id)

    def factory():
        return wrap(construct(), camera_id=camera_id, factory=construct, valid=valid)

    return NativeSourceOffer(camera_id, NativeVideoSource.admission_domain, factory, valid)
