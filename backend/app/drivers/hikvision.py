"""Hikvision-style cameras. Discovery only for now (add ONVIF controls when tested)."""
from __future__ import annotations

from .base import CameraDriver, DetectContext


class HikvisionDriver(CameraDriver):
    key = "hikvision"
    label = "Hikvision-style"
    rtsp_paths = ("/Streaming/Channels/101", "/Streaming/Channels/102")

    def matches(self, ctx: DetectContext) -> bool:
        return "hikvision" in ctx.vendor.lower()
