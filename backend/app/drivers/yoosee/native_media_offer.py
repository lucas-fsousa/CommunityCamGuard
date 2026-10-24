"""Exact-unit reviewed native-video offers, never an enrollment/brand-only grant."""

from time import time

from ...db import p2p
from ...db.registry import Camera
from ...media.source_offer import NativeSourceOffer
from ...media.source_registry import SourceUnavailable
from . import capability_profiles as profiles
from . import capability_rollout as rollout
from . import capability_snapshot_store as snapshots
from .native_video_source import NativeVideoSource


def offer(camera: Camera) -> NativeSourceOffer | None:
    camera_id = camera.camera_id
    selected = rollout.selected(camera_id)
    if selected is None or "native_video" not in selected[1]:
        return None
    identity = selected[0]
    enrollment = p2p.read_registered_enrollment(camera_id)
    if enrollment is None or enrollment.device_id != identity.device_id:
        return None

    def valid() -> bool:
        current = rollout.selected(camera_id)
        if current != selected or p2p.read_registered_enrollment(camera_id) != enrollment:
            return False
        now = time()
        if not snapshots.is_current(camera_id=camera_id, identity=identity, now=now):
            return False
        profile = profiles.load(camera_id=camera_id, identity=identity, now=now)
        proofs = [item for item in profile.operations if item.key == "native_video"] if profile else []
        return (len(proofs) == 1 and proofs[0].readable is True and proofs[0].writable is False
                and "hevc" in proofs[0].options)

    def factory():
        if not valid():
            raise SourceUnavailable("native media evidence changed")
        return NativeVideoSource(camera_id, enrollment.device_id)

    return NativeSourceOffer(camera_id, NativeVideoSource.admission_domain, factory, valid) if valid() else None
