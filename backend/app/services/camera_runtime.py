"""Shared camera lookup and live-service reconciliation operations."""

from __future__ import annotations

import logging
from typing import Any

from .. import drivers
from ..camera_identity import valid_camera_id
from ..db import registry, source_policy
from ..discovery import active_scan
from ..media import go2rtc
from ..media.reconciliation import mutation_scope
from ..media.source_registry import SourceRegistry
from .address_recovery import AddressRecovery

log = logging.getLogger(__name__)


def resolve_camera(reference: str) -> registry.Camera | None:
    """Resolve an opaque public ID with a bounded legacy-MAC fallback."""

    if valid_camera_id(reference):
        return registry.get_camera_by_id(reference)
    return registry.get_camera(reference)


def probe_and_store(camera: registry.Camera) -> registry.Camera:
    """Probe through the selected driver and persist the capability snapshot."""

    capabilities = drivers.probe(camera, active_scan.enumerate_ports(camera.last_ip))
    return registry.upsert_camera(
        camera.mac,
        camera_id=camera.camera_id,
        capabilities=capabilities.to_dict(),
    )


def runtime_statuses(request: Any, cameras: list[registry.Camera]) -> list[dict]:
    """Read media activity once and map it to configured camera identities."""

    media = getattr(request.app.state, "media", None)
    recorder = getattr(request.app.state, "rec", None)
    native = getattr(request.app.state, "native_sources", None)
    native_states = native.states() if isinstance(native,SourceRegistry) else {}
    try:
        online_probe = getattr(media, "stream_online", None)
        online_streams = online_probe() if callable(online_probe) else {}
    except (OSError, ValueError):
        online_streams = {}
    return [
        {
            "id": camera.camera_id,
            "mac": camera.mac,
            "online": (native_states[camera.camera_id]=='running' if camera.camera_id in native_states else
                       bool(online_streams.get(go2rtc.stream_id(camera.camera_id), False))),
            "recording": (bool(native and native.is_recording(camera.camera_id)) if camera.camera_id in native_states else
                          bool(recorder and recorder.is_recording(camera.camera_id))),
        }
        for camera in cameras
    ]


def resync_services(request: Any, *, address_changes: tuple[str, ...] | None = None) -> None:
    """Best-effort reconciliation of media and recorder state after registry changes."""

    media = getattr(request.app.state, "media", None)
    recorder = getattr(request.app.state, "rec", None)
    try:
        if address_changes is not None:
            if not address_changes:
                return
            selections = source_policy.read_for(address_changes)
            # Native routes do not embed registry IPs in the shared RTSP hub.
            # This is only an address-metadata update, not a source handoff or
            # evidence that a previously pending hub reload has been applied.
            if all(cid in selections and selections[cid].mode == "native"
                   for cid in address_changes):
                return
        with mutation_scope(media):
            if media is not None:
                media.restart()
                if not media.wait_healthy(timeout=6):
                    raise RuntimeError("media unavailable after registry reconciliation")
            if recorder is not None:
                recorder.start()
    except Exception as exc:  # registry changes are durable; startup reconciles later
        recovery = getattr(request.app.state, "address_recovery", None)
        if isinstance(recovery, AddressRecovery):
            recovery.request_apply()
        log.warning("service resync after registry change failed error_type=%s", type(exc).__name__)
