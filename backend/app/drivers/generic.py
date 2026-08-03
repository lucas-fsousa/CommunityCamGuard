"""Generic fallback — any RTSP camera we don't recognise. Provides the common stream paths
for discovery and the shared RTSP capability probe (video/audio tracks); no vendor controls.
This is the driver a camera gets until a more specific one claims it."""
from __future__ import annotations

from .base import CameraDriver


class GenericDriver(CameraDriver):
    key = "generic"
    label = "Generic RTSP"
    rtsp_paths = ("/live", "/live/main", "/h264", "/stream1", "/")
    # matches(): inherits False — the registry falls back to this when nothing else matches.
