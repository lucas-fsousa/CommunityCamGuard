"""Generic isolated RTSP startup and per-camera address replacement."""

from ..config import get_settings
from ..db import owned_restart, registry, source_policy
from ..media.go2rtc import Go2rtc
from ..media.owned_runtime import OwnedSourceRuntime
from ..media.rtsp_ingress import OwnedRtspIngress
from ..media.server_owner import ServerOwner
from ..media.source_registry import SourceUnavailable


class OwnedRtspService:
    def __init__(self, runtime: OwnedSourceRuntime, media: Go2rtc):
        self.runtime, self.media = runtime, media

    def _worker(self, camera_id: str) -> OwnedRtspIngress:
        camera = registry.get_camera_by_id(camera_id)
        if camera is None or not camera.rtsp_url:
            raise SourceUnavailable("configured RTSP camera required")
        return OwnedRtspIngress(camera_id, camera.rtsp_url, binary=get_settings().go2rtc_bin)

    def restore(self, owner: ServerOwner) -> dict[str, str]:
        # A new, OS-fenced container namespace certifies that old owned children
        # cannot still be capturing. Persisted mode alone is never sufficient.
        if owner.abandoned_namespace_retired:
            existing = {item.camera_id for item in owned_restart.pending()}
            for cid, selected in source_policy.read_for(c.camera_id for c in registry.list_cameras()).items():
                if cid in existing or selected.mode != 'owned_rtsp' or selected.revision < 1:
                    continue
                if selected.phase != 'blocked':
                    selected = source_policy.block(cid, expected_revision=selected.revision)
                owned_restart.save((owned_restart.CleanRestart(cid, selected.revision),))
        statuses = {}
        for checkpoint in owned_restart.pending():
            try:
                worker = self._worker(checkpoint.camera_id)
                receipt = self.runtime.retirement.resume_owned(checkpoint)
                self.runtime.activate(receipt, worker, apply_routes=lambda: None)
                statuses[checkpoint.camera_id] = 'running'
            except Exception:
                statuses[checkpoint.camera_id] = 'unavailable'
        return statuses

    def available(self, camera_id: str) -> bool:
        return self.runtime.available(camera_id)

    def refresh(self, camera_id: str) -> None:
        self.runtime.replace(self._worker(camera_id),
                             apply_routes=lambda: self.media.patch_owned_source(camera_id))
