"""Dahua-style discovery and RTSP candidates; device controls are not implemented.

Do not infer a camera's ONVIF endpoint or control support from the vendor label.
"""
from __future__ import annotations

from ..base import CameraDriver, DetectContext


class DahuaDriver(CameraDriver):
    key = "dahua"
    label = "Dahua-style"
    rtsp_paths = ("/cam/realmonitor?channel=[CHANNEL]&subtype=0",
                  "/cam/realmonitor?channel=[CHANNEL]&subtype=1")

    def matches(self, ctx: DetectContext) -> bool:
        return "dahua" in ctx.vendor.lower()
