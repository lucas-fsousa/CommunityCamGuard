"""Recover reviewed native intent only after OS-fenced previous namespace retirement."""

from ..db import native_restart, registry, source_policy
from ..media.server_owner import ServerOwner


def checkpoints(owner: ServerOwner, offer_for, *, limit: int | None = 1) -> dict[str, str]:
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("bounded native checkpoint capacity required")
    if not owner.abandoned_namespace_retired:
        return {}
    existing = {item.camera_id for item in native_restart.pending()}
    remaining = max(0, limit - len(existing)) if limit is not None else None
    if remaining == 0:
        return {}
    statuses: dict[str, str] = {}
    cameras = registry.list_cameras()
    policies = source_policy.read_for(camera.camera_id for camera in cameras)
    for camera in cameras:
        policy = policies[camera.camera_id]
        if camera.camera_id in existing or policy.mode != 'native' or policy.revision < 1:
            continue
        offer = offer_for(camera)
        if offer is None or offer.valid() is not True:
            statuses[camera.camera_id] = 'evidence_unavailable'
            continue
        # The old namespace init could not release the server lock while uncertain
        # services remained alive. Its death kills that namespace's orphan children.
        # Do not synthesize a checkpoint for a still-running owner or a host orphan.
        if policy.phase != 'blocked':
            policy = source_policy.block(camera.camera_id, expected_revision=policy.revision)
        native_restart.save((native_restart.CleanRestart(camera.camera_id, policy.revision, offer.domain),))
        if remaining is not None:
            remaining -= 1
            if not remaining:
                break
    return statuses
