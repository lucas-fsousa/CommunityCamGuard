"""Read-only source availability. Persisted intent is never a restart receipt."""

from collections.abc import Iterable

from ..db import source_policy
from .owned_bindings import OwnedIngressBindings
from .source_registry import SourceRegistry


def describe_sources(camera_ids: Iterable[str], *, owned: OwnedIngressBindings | None = None,
                     native: SourceRegistry | None = None) -> dict[str, dict[str, str | int | bool]]:
    result: dict[str, dict[str, str | int | bool]] = {}
    with source_policy.snapshot(camera_ids) as selections:
        reservations = native.states() if native is not None else {}
        for camera_id, selection in selections.items():
            phase = reservations.get(camera_id)
            available = False
            if selection.phase != "active":
                status = selection.phase
            elif selection.mode == "native":
                status = phase or "runtime_missing"
                available = phase == "running"
            elif phase is not None:
                status = "runtime_conflict"  # do not advertise hub fallback over a native claim
            elif selection.mode == "owned_rtsp":
                available = owned is not None and owned.binding(camera_id, selection) is not None
                status = "owned" if available else "runtime_missing"
            else:
                status, available = "legacy", True
            result[camera_id] = {
                "mode": selection.mode, "phase": selection.phase, "revision": selection.revision,
                "status": status, "available": available,
            }
    return result
