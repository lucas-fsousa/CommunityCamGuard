"""Yoosee / generic HiSilicon Wi-Fi cameras (the "Gwell" family).

These cheap cams expose an ONVIF service on the **non-standard port 5000** (not 80/8000):
PTZ via ``ContinuousMove`` and device info via ``GetDeviceInformation`` (see
:mod:`..control.ptz` / :mod:`..control.device`). Video + PCMA audio over RTSP ``/onvif1``.
PTZ is a fixed ~0.4s step (the UI repeats it while held); reboot and two-way audio live in
the proprietary Gwell P2P channel (port 50000) and are **not** reachable over ONVIF — see
docs/DECISIONS.md — so they are not advertised here.

Confirmed on the project's two field units (model ``IPC``, firmware ``40.01.22``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..control import device, media, ptz
from .base import CameraDriver, Capabilities, DetectContext

if TYPE_CHECKING:
    from ..db.registry import Camera


class YooseeDriver(CameraDriver):
    key = "yoosee"
    label = "Yoosee / generic HiSilicon (ONVIF port 5000)"
    rtsp_paths = ("/onvif1", "/onvif2", "/11", "/12", "/live.sdp", "/0", "/1")
    transport = "udp"                       # tiny embedded server; UDP only
    features = frozenset({"ptz", "audio_in"})   # reboot/audio_out are Gwell-P2P-only (roadmap)

    def matches(self, ctx: DetectContext) -> bool:
        vendor = ctx.vendor.lower()
        return ("rtspserver" in vendor or "yoosee" in vendor or "hisilicon" in vendor
                or (5000 in ctx.open_ports and 554 in ctx.open_ports))

    def _probe_controls(self, camera: Camera, caps: Capabilities) -> None:
        ip = camera.last_ip
        if not ip:
            return
        if ptz.supports_ptz(ip):            # read-only zero-velocity move; never pans
            caps.ptz = True
            caps.ptz_protocol = "onvif"
        info = device.info(ip)              # model / firmware from the ONVIF device service
        if info is not None:
            caps.model = info.get("model", "")
            caps.firmware = info.get("firmware", "")
        # Ask the ONVIF media service for the camera's real RTSP paths (GetProfiles +
        # GetStreamUri) instead of trusting the hard-coded guesses. Empty if the media
        # service is absent/partial, in which case discovery falls back to `rtsp_paths`.
        caps.stream_paths = media.stream_paths(ip)

    def ptz(self, camera: Camera, direction: str | None, action: str = "step") -> bool:
        if action == "stop":
            return ptz.halt(camera)
        if action == "start":
            return ptz.start(camera, direction)
        return ptz.move(camera, direction)

    # reboot(): inherits Unsupported — these units ignore ONVIF SystemReboot (Gwell P2P only).
