"""Driver registry — the plug-in point for camera brands/models.

To add support for a new family: write a :class:`~.base.CameraDriver` subclass in this
package and add it to :data:`DRIVERS` below (most-specific first; the generic fallback stays
last). Everything else — discovery paths, the capability probe, PTZ/reboot routing — flows
through here automatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import CameraDriver, Capabilities, DetectContext, Unsupported, classify_ports
from .contracts import (
    AudioMessageResult,
    ControlDescriptor,
    ControlNotReady,
    ControlOperationError,
    ControlOption,
    ControlResult,
    ControlValue,
    Weekday,
    WeeklySchedule,
)
from .dahua import DahuaDriver
from .generic import GenericDriver
from .hikvision import HikvisionDriver
from .xiongmai import XiongmaiDriver
from .yoosee import YooseeDriver

if TYPE_CHECKING:
    from ..db.registry import Camera
    from .onboarding import OnboardingPort

# Ordered most-specific first; the generic fallback must stay last (it matches nothing itself).
DRIVERS: tuple[CameraDriver, ...] = (
    YooseeDriver(),
    DahuaDriver(),
    HikvisionDriver(),
    XiongmaiDriver(),
    GenericDriver(),
)


def _index_drivers(registered: tuple[CameraDriver, ...]) -> dict[str, CameraDriver]:
    """Validate deterministic registry invariants before exposing any camera routing."""

    if not registered or registered[-1].key != "generic":
        raise RuntimeError("the generic camera driver must be registered exactly once and last")
    keys = [driver.key for driver in registered]
    if keys.count("generic") != 1:
        raise RuntimeError("the generic camera driver must be registered exactly once and last")
    if len(keys) != len(set(keys)):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        raise RuntimeError(f"duplicate camera driver key(s): {', '.join(duplicates)}")
    return {driver.key: driver for driver in registered}


_BY_KEY = _index_drivers(DRIVERS)
GENERIC: CameraDriver = _BY_KEY["generic"]

__all__ = [
    "DRIVERS",
    "GENERIC",
    "AudioMessageResult",
    "CameraDriver",
    "Capabilities",
    "ControlDescriptor",
    "ControlNotReady",
    "ControlOperationError",
    "ControlOption",
    "ControlResult",
    "ControlValue",
    "DetectContext",
    "Unsupported",
    "Weekday",
    "WeeklySchedule",
    "classify_ports",
    "detect",
    "for_camera",
    "get",
    "init_onboarding",
    "onboarding_provider",
    "onboarding_providers",
    "probe",
    "rtsp_paths",
    "rtsp_paths_for",
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

    return detect(
        DetectContext(
            vendor=getattr(camera, "vendor", "") or "",
            model=str(caps.get("model") or ""),
            firmware=str(caps.get("firmware") or ""),
            open_ports=caps.get("open_ports") or [],
        )
    )


def onboarding_provider(driver_key: str | None = None):
    """Resolve a driver-owned onboarding port without importing a vendor package upstream."""

    entries = _onboarding_entries()
    if driver_key is not None:
        provider = dict(entries).get(driver_key)
        if provider is None:
            raise LookupError(f"driver {driver_key!r} does not support factory onboarding")
        return provider
    providers = [provider for _key, provider in entries]
    if len(providers) != 1:
        raise LookupError("an explicit onboarding driver is required")
    return providers[0]


def onboarding_providers() -> tuple[tuple[str, OnboardingPort], ...]:
    """List explicitly registered factory-onboarding providers by stable driver key."""

    return _onboarding_entries()


def _onboarding_entries() -> tuple[tuple[str, OnboardingPort], ...]:
    entries: list[tuple[str, OnboardingPort]] = []
    for driver in DRIVERS:
        provider = driver.onboarding()
        if provider is None:
            continue
        if provider.driver_key != driver.key:
            raise RuntimeError(
                f"onboarding provider {provider.provider!r} declares driver key "
                f"{provider.driver_key!r}, expected {driver.key!r}"
            )
        entries.append((driver.key, provider))
    return tuple(entries)


def init_onboarding() -> None:
    """Initialize durable stores owned by registered onboarding providers."""

    seen: set[int] = set()
    for _driver_key, provider in _onboarding_entries():
        if id(provider) not in seen:
            provider.init()
            seen.add(id(provider))


def detect(ctx: DetectContext) -> CameraDriver:
    """Pick the highest-confidence driver, preserving registration order for exact ties."""

    selected = GENERIC
    selected_confidence = 0
    for driver in DRIVERS:
        if driver is GENERIC:
            continue
        confidence = max(0, min(100, int(driver.match_confidence(ctx))))
        if confidence > selected_confidence:
            selected = driver
            selected_confidence = confidence
    return selected


def _fill(template: str, username: str, password: str, channel: int) -> str:
    return (
        template.replace("[USERNAME]", username)
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


def rtsp_paths_for(
    driver_key: str | None,
    username: str = "",
    password: str = "",
    channel: int = 1,
    discovered: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Return camera-reported paths plus only the selected family's guesses.

    Unknown/generic hosts retain the full compatibility union because no family evidence exists.
    Identified cameras are never probed with every other installed driver's paths.
    """

    have_creds = bool(username and password)
    selected = get(driver_key)
    families = DRIVERS if selected is GENERIC else (selected,)
    candidates = [path for path in discovered if isinstance(path, str) and path.startswith("/")]
    for driver in families:
        for template in driver.rtsp_paths:
            if "[PASSWORD]" in template and not have_creds:
                continue
            candidates.append(_fill(template, username, password, channel))
    return list(dict.fromkeys(candidates))


def probe(camera: Camera, open_ports: list[int] | None = None) -> Capabilities:
    """Detect the camera's driver (from its vendor + open ports) and probe with it."""
    ctx = DetectContext(
        vendor=getattr(camera, "vendor", "") or "",
        model=str((getattr(camera, "capabilities", None) or {}).get("model") or ""),
        firmware=str((getattr(camera, "capabilities", None) or {}).get("firmware") or ""),
        open_ports=sorted(set(open_ports or [])),
    )
    return detect(ctx).probe(camera, open_ports)
