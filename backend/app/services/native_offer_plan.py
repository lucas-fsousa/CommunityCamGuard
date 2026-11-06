"""Driver-neutral startup planning from existing native intent only. No migration."""

import logging

from .. import drivers
from ..db import registry, source_policy
from ..drivers.base import CameraDriver
from ..media.source_offer import NativeSourceOffer
from ..recording.recorder import Recorder

log = logging.getLogger(__name__)


def plan(recorder: Recorder) -> dict[str, NativeSourceOffer]:
    cameras = registry.list_cameras()
    policies = source_policy.read_for(camera.camera_id for camera in cameras)
    groups: dict[int, tuple[CameraDriver, list[registry.Camera]]] = {}
    for camera in cameras:
        if policies[camera.camera_id].mode != "native":
            continue
        driver = drivers.for_camera(camera)
        groups.setdefault(id(driver), (driver, []))[1].append(camera)
    result: dict[str, NativeSourceOffer] = {}
    for driver, members in groups.values():
        try:
            archives = {camera.camera_id: recorder.native_archive(camera.camera_id) for camera in members}
            batch = getattr(driver, "native_media_offers", None)
            # Compatibility with existing optional single-offer adapters.
            offered = (batch(tuple(members), archives=archives) if callable(batch) else
                       tuple(item for camera in members if (item := driver.native_media_offer(
                           camera, archive=archives[camera.camera_id])) is not None))
            ids = {camera.camera_id for camera in members}
            if (not isinstance(offered, tuple) or len(offered) > len(members)
                    or any(not isinstance(item, NativeSourceOffer) or item.camera_id not in ids for item in offered)
                    or len({item.camera_id for item in offered}) != len(offered)):
                raise ValueError("driver returned an invalid native plan")
            result.update((item.camera_id, item) for item in offered)
        except Exception:
            log.warning("driver native startup plan unavailable")  # No credentials/payload logging.
    return result
