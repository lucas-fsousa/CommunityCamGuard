"""Driver registry — the plug-in point for camera brands/models.

To add support for a new family: write a :class:`~.base.CameraDriver` subclass in this
package and add it to :data:`DRIVERS` below (most-specific first; the generic fallback stays
last). Everything else — discovery paths, the capability probe, PTZ/reboot routing — flows
through here automatically.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import CameraDriver, Capabilities, DetectContext, Unsupported, classify_ports
from .dahua import DahuaDriver
from .generic import GenericDriver
from .hikvision import HikvisionDriver
from .xiongmai import XiongmaiDriver
from .yoosee import YooseeDriver

if TYPE_CHECKING:
    from ..db.registry import Camera

# Ordered most-specific first; the generic fallback must stay last (it matches nothing itself).
DRIVERS: tuple[CameraDriver, ...] = (
    YooseeDriver(),
    DahuaDriver(),
    HikvisionDriver(),
    XiongmaiDriver(),
    GenericDriver(),
)
_BY_KEY: dict[str, CameraDriver] = {d.key: d for d in DRIVERS}
GENERIC: CameraDriver = _BY_KEY["generic"]

__all__ = [
    "DRIVERS",
    "GENERIC",
    "CameraDriver",
    "Capabilities",
    "DetectContext",
    "Unsupported",
    "classify_ports",
    "detect",
    "for_camera",
    "get",
    "probe",
    "rtsp_paths",
]


def get(key: str | None) -> CameraDriver:
    """The driver for ``key`` (falls back to the generic driver)."""
    return _BY_KEY.get(key or "", GENERIC)


def for_camera(camera: Camera) -> CameraDriver:
    """The driver a stored camera belongs to.

    Prefers the ``driver`` key stored at probe time; falls back to detecting from the camera's
    vendor + known open ports (so cameras probed before drivers existed, or not yet probed, still
    route to the right driver).
    """
    caps = getattr(camera, "capabilities", None) or {}
    if caps.get("driver"):
        return get(caps["driver"])

    return detect(DetectContext(
        vendor=getattr(camera, "vendor", "") or "",
        open_ports=caps.get("open_ports") or []
    ))


def detect(ctx: DetectContext) -> CameraDriver:
    """Pick the most specific driver that claims ``ctx`` (generic if none)."""
    return next(
        iter(driver for driver in DRIVERS if driver is not GENERIC and driver.matches(ctx)),
        GENERIC
    )


def _fill(template: str, username: str, password: str, channel: int) -> str:
    return (template
        .replace("[USERNAME]", username)
        .replace("[PASSWORD]", password)
        .replace("[CHANNEL]", str(channel))
    )


def rtsp_paths(username: str = "", password: str = "", channel: int = 1) -> list[str]:
    """Ordered, de-duplicated RTSP paths to probe across every driver (most-common first).

    Templates that embed a password are emitted only when credentials are supplied.
    """
    have_creds = bool(username and password)
    seen: set[str] = set()
    out: list[str] = []
    for driver in DRIVERS:
        for template in driver.rtsp_paths:
            if "[PASSWORD]" in template and not have_creds:
                continue
            path = _fill(template, username, password, channel)
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def probe(camera: Camera, open_ports: list[int] | None = None) -> Capabilities:
    """Detect the camera's driver (from its vendor + open ports) and probe with it."""
    ctx = DetectContext(
        vendor=getattr(camera, "vendor", "") or "",
        open_ports=sorted(set(open_ports or []))
    )
    return detect(ctx).probe(camera, open_ports)
