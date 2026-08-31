"""Select the native Yoosee player family from its stable device identity."""

from __future__ import annotations

from enum import StrEnum


class PlayerFamily(StrEnum):
    LEGACY_GW = "legacy_gw"
    IOTVIDEO = "iotvideo"


def player_family(device_id: str | int) -> PlayerFamily:
    """Mirror the APK's ``DeviceUtils`` split used by ``PlayerFactory``.

    Numeric IDs outside the unsigned 32-bit range are routed to IoTVideo's
    ``LivePlayer``.  Older IDs retain the GW/AMR implementation.
    """

    try:
        numeric_id = int(device_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Yoosee device ID must be numeric") from exc
    if numeric_id <= 0:
        raise ValueError("Yoosee device ID must be positive")
    return PlayerFamily.IOTVIDEO if numeric_id > 0xFFFFFFFF else PlayerFamily.LEGACY_GW
