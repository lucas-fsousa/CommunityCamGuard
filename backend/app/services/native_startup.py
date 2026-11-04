"""Opt-in restart of reviewed, durably retired native sources. No first migration."""

import logging
from collections.abc import Callable

from .. import drivers
from ..db import native_restart, registry
from ..media.native_runtime import NativeSourceRuntime
from ..media.owned_retirement import OwnedRetirementCoordinator
from ..media.server_owner import ServerOwner
from ..media.source_registry import SourceCleanupUnconfirmed
from ..recording.recorder import Recorder

log=logging.getLogger(__name__)


def restore_reviewed(runtime: NativeSourceRuntime, retirement: OwnedRetirementCoordinator,
                     recorder: Recorder, apply_routes: Callable[[], None], *,
                     owner: ServerOwner | None = None) -> dict[str,str]:
    def offer_for(camera):
        return drivers.for_camera(camera).native_media_offer(camera, archive=recorder.native_archive(camera.camera_id))
    from .abandoned_native import checkpoints
    statuses = checkpoints(owner, offer_for) if owner is not None else {}
    for checkpoint in native_restart.pending():
        camera=registry.get_camera_by_id(checkpoint.camera_id)
        if camera is None:
            continue
        try:
            offer=offer_for(camera)
            if offer is None or offer.valid() is not True:
                statuses[camera.camera_id]='evidence_unavailable'
                continue  # Keep the unspent clean checkpoint; never fall back to RTSP.
            runtime.restore(retirement=retirement,checkpoint=checkpoint,offer=offer,
                            apply_routes=apply_routes,ready_timeout=30,
                            background=owner is not None and owner.namespace_init)
            statuses[camera.camera_id]='starting' if owner is not None and owner.namespace_init else 'running'
        except SourceCleanupUnconfirmed:
            statuses[camera.camera_id]='cleanup_unconfirmed'
        except Exception:
            statuses[camera.camera_id]='unavailable'
        log.info("native restart camera_id=%s status=%s",camera.camera_id,statuses[camera.camera_id])
        break  # Current native capacity is one; never burn another checkpoint.
    return statuses
