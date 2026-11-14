"""Background DHCP recovery independent of browser activity and camera brand."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..config import get_settings
from ..db import registry, source_policy
from ..discovery.address_refresh import address_matches, find_addresses
from ..media.go2rtc import stream_id
from ..media.reconciliation import mutation_scope
from .owned_rtsp import OwnedRtspService
from .thread_shutdown import join_thread, require_retired

log = logging.getLogger(__name__)


class AddressRecovery:
    """Debounced missing/wrong identity, five-minute scans, one worker lifecycle."""

    def __init__(self, media: Any, recorder: Any, *, owned: OwnedRtspService | None = None) -> None:
        self.media = media
        self.recorder = recorder
        self.owned = owned
        self._owned_pending: set[str] = set()
        self._owned_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._missing: dict[str, int] = {}
        self._next_scan = 0.0
        self._next_identity_check = 0.0
        self._identity_mismatch: set[str] = set()
        self._pending_apply = threading.Event()
        self._applying = threading.Lock()

    def request_apply(self) -> None:
        """Coalesce deferred registry/config changes into the existing worker."""
        self._pending_apply.set()

    def request_owned_apply(self, camera_ids: tuple[str, ...]) -> None:
        with self._owned_lock:
            self._owned_pending.update(camera_ids)

    def _apply_owned_pending(self) -> bool:
        with self._owned_lock:
            pending = tuple(self._owned_pending)
            self._owned_pending.clear()
        for cid in pending:
            if self._stop.is_set():
                self.request_owned_apply((cid,))
                continue
            try:
                selected = source_policy.read_for((cid,))[cid]
                if selected.mode != 'owned_rtsp' or self.owned is None:
                    continue
                self.owned.refresh(cid)
            except Exception as exc:
                self.request_owned_apply((cid,))
                log.warning("isolated RTSP recovery deferred camera_id=%s error_type=%s", cid, type(exc).__name__)
        return bool(pending)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dhcp-recovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        join_thread(self._thread, timeout=8)
        require_retired(self._thread)

    def _run(self) -> None:
        while not self._stop.wait(30):
            try:
                self.tick()
            except Exception as exc:
                log.warning("DHCP recovery failed error_type=%s", type(exc).__name__)

    def _apply(self) -> bool:
        # Batch all changed addresses into the existing media reconciliation path.
        # External reload failure must remain pending even though DB/config are durable.
        with mutation_scope(self.media):
            if self._stop.is_set():
                return False
            self.media.restart()
            if not self.media.wait_healthy(timeout=6):
                raise RuntimeError("media unavailable after address refresh")
            if self._stop.is_set():
                return False  # never revive a recorder after service shutdown
            if not self.recorder.paused:
                self.recorder.start()
            return True

    def _apply_pending(self) -> None:
        if not self._applying.acquire(blocking=False):
            return
        try:
            if not self._pending_apply.is_set():
                return
            self._pending_apply.clear()  # requests during apply remain set for next tick
            try:
                if not self._apply():
                    self._pending_apply.set()
            except Exception:
                self._pending_apply.set()
                raise
        finally:
            self._applying.release()

    def tick(self) -> None:
        if self._apply_owned_pending():
            return
        if self._pending_apply.is_set():
            self._apply_pending()
            return
        cameras = registry.list_cameras()
        selections=source_policy.read_for(cam.camera_id for cam in cameras)
        cameras=[cam for cam in cameras if selections[cam.camera_id].permits_legacy_rtsp
                 or (self.owned is not None and selections[cam.camera_id].mode == 'owned_rtsp')]
        online = self.media.stream_online()
        if self.owned is not None:
            for cam in cameras:
                if selections[cam.camera_id].mode == 'owned_rtsp' and not self.owned.available(cam.camera_id):
                    online[stream_id(cam.camera_id)] = False
        now = time.monotonic()
        if now >= self._next_identity_check and not self._stop.is_set():
            self._next_identity_check = now + 300
            mismatched = set()
            for cam in cameras:
                if self._stop.is_set():
                    return
                if (cam.mac and cam.last_ip and cam.rtsp_url and online.get(stream_id(cam.camera_id))
                        and address_matches(cam.last_ip, cam.mac, self._stop) is False):
                    mismatched.add(cam.camera_id)
            self._identity_mismatch = mismatched
        self._missing = {
            cam.camera_id: (0 if online.get(stream_id(cam.camera_id)) and cam.camera_id not in self._identity_mismatch else
                            self._missing.get(cam.camera_id, 0) + 1)
            for cam in cameras if cam.mac and cam.rtsp_url
        }
        missing = [cam for cam in cameras if self._missing.get(cam.camera_id, 0) >= 2]
        # A dead isolated process at an independently reconfirmed address needs
        # no whole-subnet scan and must not wait out another camera's scan cooldown.
        if self.owned is not None:
            for cam in missing:
                if (selections[cam.camera_id].mode == 'owned_rtsp'
                        and not self.owned.available(cam.camera_id)
                        and address_matches(cam.last_ip, cam.mac, self._stop) is True):
                    self.request_owned_apply((cam.camera_id,))
            if self._apply_owned_pending():
                return
        if not missing or time.monotonic() < self._next_scan or self._stop.is_set():
            return
        self._next_scan = time.monotonic() + 300
        log.info("DHCP recovery lookup started cameras=%d", len(missing))
        found = find_addresses(
            {cam.mac for cam in missing}, get_settings().scan_subnets, self._stop,
        )
        if self._stop.is_set():
            return
        for cam in missing:
            ip = found.get(cam.mac.lower())
            if ip:
                self._identity_mismatch.discard(cam.camera_id)
                self._missing[cam.camera_id] = 0
            if ip and ip != cam.last_ip and registry.refresh_address(cam, ip):
                if selections[cam.camera_id].mode == 'owned_rtsp':
                    self.request_owned_apply((cam.camera_id,))
                else:
                    self.request_apply()
                log.warning("DHCP address updated camera=%s", cam.camera_id)
            elif ip and selections[cam.camera_id].mode == 'owned_rtsp':
                self.request_owned_apply((cam.camera_id,))
        self._apply_owned_pending()
        if self._pending_apply.is_set():
            self._apply_pending()
