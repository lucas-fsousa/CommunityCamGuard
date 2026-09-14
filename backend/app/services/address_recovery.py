"""Background DHCP recovery independent of browser activity and camera brand."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..config import get_settings
from ..db import registry
from ..discovery.address_refresh import find_addresses
from ..media.go2rtc import stream_id

log = logging.getLogger(__name__)


class AddressRecovery:
    """Two offline observations, one scan per five minutes, one worker lifecycle."""

    def __init__(self, media: Any, recorder: Any) -> None:
        self.media = media
        self.recorder = recorder
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._missing: dict[str, int] = {}
        self._next_scan = 0.0
        self._pending_apply = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dhcp-recovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8)

    def _run(self) -> None:
        while not self._stop.wait(30):
            try:
                self.tick()
            except Exception as exc:
                log.warning("DHCP recovery failed error_type=%s", type(exc).__name__)

    def _apply(self) -> None:
        # Batch all changed addresses into the existing media reconciliation path.
        # External reload failure must remain pending even though DB/config are durable.
        self.media.restart()
        if not self.media.wait_healthy(timeout=6):
            raise RuntimeError("media unavailable after address refresh")
        if not self.recorder.paused:
            self.recorder.start()
        self._pending_apply = False

    def tick(self) -> None:
        if self._pending_apply:
            self._apply()
            return
        cameras = registry.list_cameras()
        online = self.media.stream_online()
        self._missing = {
            cam.camera_id: (0 if online.get(stream_id(cam.camera_id)) else
                            self._missing.get(cam.camera_id, 0) + 1)
            for cam in cameras if cam.mac and cam.rtsp_url
        }
        missing = [cam for cam in cameras if self._missing.get(cam.camera_id, 0) >= 2]
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
            if ip and ip != cam.last_ip and registry.refresh_address(cam, ip):
                self._pending_apply = True
                log.warning("DHCP address updated camera=%s old_ip=%s new_ip=%s",
                            cam.camera_id, cam.last_ip, ip)
        if self._pending_apply:
            self._apply()
