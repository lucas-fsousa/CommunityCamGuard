"""Single bounded worker for optional, driver-owned read-only control preparation."""
from __future__ import annotations

import logging
import threading
import time

from .. import drivers
from ..db import registry

log = logging.getLogger(__name__)


class ControlSessions:
    """No camera actions, concurrent warmups, retries in a tight loop or media links."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures: dict[str, tuple[int, float]] = {}
        self._prepared: set[str] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="control-sessions", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        # The worker owns cleanup, including when its bounded network read is
        # still finishing. Never close a socket under an in-flight preparation.

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    self.tick()
                except Exception as exc:
                    log.warning("control_session registry_error=%s", type(exc).__name__)
                # Preparation itself may take seconds. Do not add a full extra
                # interval and let the other cameras' 8s idle leases lapse.
                if self._stop.wait(max(1.0, 3.0 - (time.monotonic() - started))):
                    break
        finally:
            for driver in drivers.DRIVERS:
                driver.close_control_sessions()

    def tick(self) -> None:
        cameras = registry.list_cameras()
        ids = {camera.camera_id for camera in cameras}
        self._failures = {key: value for key, value in self._failures.items() if key in ids}
        self._prepared.intersection_update(ids)
        for camera in cameras:
            if self._stop.is_set():
                break
            failures, retry_at = self._failures.get(camera.camera_id, (0, 0.0))
            if time.monotonic() < retry_at:
                continue
            try:
                prepared = drivers.for_camera(camera).maintain_control_session(camera)
            except Exception as exc:
                failures = min(5, failures + 1)
                delay = min(300, 15 * 2 ** failures)
                self._failures[camera.camera_id] = (failures, time.monotonic() + delay)
                self._prepared.discard(camera.camera_id)
                log.warning("control_session prepare_failed error_type=%s retry_s=%d",
                            type(exc).__name__, delay)
            else:
                self._failures.pop(camera.camera_id, None)
                if prepared:
                    if camera.camera_id not in self._prepared:
                        log.info("control_session prepared camera=%s", camera.camera_id)
                    self._prepared.add(camera.camera_id)
                else:
                    self._prepared.discard(camera.camera_id)
