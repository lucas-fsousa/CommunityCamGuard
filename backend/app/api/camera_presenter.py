"""Stable public representation of registry cameras."""

from __future__ import annotations

from .. import drivers
from ..db import registry
from ..media import go2rtc
from ..services import control_catalog


def camera_out(camera: registry.Camera) -> dict:
    """Serialize one camera without leaking its stored password."""

    controls = control_catalog(camera)
    driver = drivers.for_camera(camera)
    audio_messages = driver.supports_audio_messages(camera)
    audio_streams = driver.supports_audio_streams(camera)
    return {
        "id": camera.camera_id,
        "mac": camera.mac,
        "name": camera.name,
        "username": camera.username,
        "has_password": bool(camera.password),
        "stream_path": camera.stream_path,
        "rtsp_port": camera.rtsp_port,
        "last_ip": camera.last_ip,
        "vendor": camera.vendor,
        "capabilities": camera.capabilities,
        "has_audio": bool(camera.capabilities.get("has_audio")),
        "stream_id": go2rtc.stream_id(camera.camera_id),
        "web_stream_id": go2rtc.web_stream_id(camera.camera_id),
        "hd_stream_id": go2rtc.hd_stream_id(camera.camera_id),
        "has_substream": camera.substream_url is not None,
        "has_quality_variants": True,
        "controls": controls,
        "audio_messages": audio_messages,
        "audio_streams": audio_streams,
        # Temporary compatibility projection; ``controls`` is authoritative.
        "vendor_controls": {key: True for key in controls},
        "webrtc_url": go2rtc.webrtc_page_url(camera.camera_id),
        "recording": False,
        "online": False,
    }
