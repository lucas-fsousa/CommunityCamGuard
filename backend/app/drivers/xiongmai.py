"""XiongMai / XMEye cameras — credentials embedded in the RTSP path (only tried when a
username/password is known). Discovery only for now."""
from __future__ import annotations

from .base import CameraDriver, DetectContext


class XiongmaiDriver(CameraDriver):
    key = "xiongmai"
    label = "XiongMai / XMEye (credentials in path)"
    rtsp_paths = ("/user=[USERNAME]&password=[PASSWORD]&channel=[CHANNEL]&stream=0.sdp?",)

    def matches(self, ctx: DetectContext) -> bool:
        v = ctx.vendor.lower()
        return "xiongmai" in v or "xmeye" in v
