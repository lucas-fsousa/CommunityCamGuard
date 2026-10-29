"""Opt-in restart of reviewed, durably retired native sources. No first migration."""

import logging
from collections.abc import Callable

from .. import drivers
from ..db import native_restart, registry
from ..media.native_runtime import NativeSourceRuntime
from ..media.owned_retirement import OwnedRetirementCoordinator
from ..media.source_registry import SourceCleanupUnconfirmed
from ..recording.recorder import Recorder

log=logging.getLogger(__name__)


def restore_reviewed(runtime: NativeSourceRuntime, retirement: OwnedRetirementCoordinator,
                     recorder: Recorder, apply_routes: Callable[[], None]) -> dict[str,str]:
    statuses={}
    for checkpoint in native_restart.pending():
        camera=registry.get_camera_by_id(checkpoint.camera_id)
        if camera is None:
            continue
        try:
            offer=drivers.for_camera(camera).native_media_offer(camera,archive=recorder.native_archive(camera.camera_id))
            if offer is None or offer.valid() is not True:
                statuses[camera.camera_id]='evidence_unavailable'
                continue  # Keep the unspent clean checkpoint; never fall back to RTSP.
            runtime.restore(retirement=retirement,checkpoint=checkpoint,offer=offer,
                            apply_routes=apply_routes,ready_timeout=30)
            statuses[camera.camera_id]='running'
        except SourceCleanupUnconfirmed:
            statuses[camera.camera_id]='cleanup_unconfirmed'
        except Exception:
            statuses[camera.camera_id]='unavailable'
        log.info("native restart camera_id=%s status=%s",camera.camera_id,statuses[camera.camera_id])
        break  # Current native capacity is one; never burn another checkpoint.
    return statuses
