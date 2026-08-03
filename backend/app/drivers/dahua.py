"""Dahua-style cameras. Discovery only for now — a contributor with the hardware can add
ONVIF PTZ/reboot (standard ONVIF on port 80) by overriding ``_probe_controls`` + ``ptz`` /
``reboot`` (the :mod:`..control` ONVIF toolbox already has the SOAP ops)."""
from __future__ import annotations

from .base import CameraDriver, DetectContext


class DahuaDriver(CameraDriver):
    key = "dahua"
    label = "Dahua-style"
    rtsp_paths = ("/cam/realmonitor?channel=[CHANNEL]&subtype=0",
                  "/cam/realmonitor?channel=[CHANNEL]&subtype=1")

    def matches(self, ctx: DetectContext) -> bool:
        return "dahua" in ctx.vendor.lower()
