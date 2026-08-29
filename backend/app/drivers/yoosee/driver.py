"""Yoosee / generic HiSilicon Wi-Fi cameras (the Gwell family).

These cameras expose ONVIF on port 5000 and proprietary controls through Gwell P2P.  Both
transports are private implementation details of this driver package; callers use only the
semantic :class:`CameraDriver` contract.

Confirmed on the project's field units (model ``IPC``, firmware ``40.01.22``/``40.1.14``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...control import device, media, ptz
from ..base import CameraDriver, Capabilities, DetectContext
from ..contracts import ControlDescriptor, ControlResult, ControlValue
from . import controls

if TYPE_CHECKING:
    from ...db.registry import Camera
    from ..onboarding import OnboardingPort


class YooseeDriver(CameraDriver):
    key = "yoosee"
    label = "Yoosee / generic HiSilicon (ONVIF port 5000)"
    rtsp_paths = ("/onvif1", "/onvif2", "/11", "/12", "/live.sdp", "/0", "/1")
    transport = "udp"
    features = frozenset({"ptz", "audio_in", "audio_out", "led", "siren"})

    def onboarding(self) -> OnboardingPort:
        from .onboarding import ONBOARDING

        return ONBOARDING

    def matches(self, ctx: DetectContext) -> bool:
        return self.match_confidence(ctx) > 0

    def match_confidence(self, ctx: DetectContext) -> int:
        vendor = ctx.vendor.lower()
        model = ctx.model.lower()
        if any(marker in vendor for marker in ("rtspserver", "yoosee", "hisilicon")):
            return 100
        if "gwell" in vendor or "yoosee" in model:
            return 95
        if 5000 in ctx.open_ports and 554 in ctx.open_ports:
            return 60
        return 0

    def _probe_controls(self, camera: Camera, caps: Capabilities) -> None:
        ip = camera.last_ip
        if not ip:
            return
        if ptz.supports_ptz(ip):
            caps.ptz = True
            caps.ptz_protocol = "onvif"
        info = device.info(ip)
        if info is not None:
            caps.model = info.get("model", "")
            caps.firmware = info.get("firmware", "")
        caps.stream_paths = media.stream_paths(ip)

    def ptz(self, camera: Camera, direction: str | None, action: str = "step") -> bool:
        if action == "stop":
            return ptz.halt(camera)
        if action == "start":
            return ptz.start(camera, direction)
        return ptz.move(camera, direction)

    def control_catalog(self, camera: Camera) -> tuple[ControlDescriptor, ...]:
        return controls.catalog(camera)

    def read_control(self, camera: Camera, key: str) -> ControlResult:
        return controls.read(camera, key)

    def write_control(self, camera: Camera, key: str, value: ControlValue) -> ControlResult:
        return controls.write(camera, key, value)
