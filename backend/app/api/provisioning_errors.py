"""Safe public projections; never derive messages from provider exception text."""

from fastapi import HTTPException

from ..provisioning.wifi import WifiSelectionError, WifiSelectionReason

_WIFI_MESSAGES = {
    WifiSelectionReason.INVALID: "invalid Wi-Fi selection; select the network again",
    WifiSelectionReason.EXPIRED: "Wi-Fi selection expired; scan again",
    WifiSelectionReason.SSID_LENGTH: "SSID must contain 1 to 32 UTF-8 bytes",
    WifiSelectionReason.SECURITY: "unsupported Wi-Fi security",
}


def wifi_selection_failure(error: WifiSelectionError) -> HTTPException:
    reason = error.reason
    # Unknown/custom reason values are never serialized (or used as mapping keys).
    if not isinstance(reason, WifiSelectionReason):
        reason = WifiSelectionReason.INVALID
    return HTTPException(
        status_code=422, detail=_WIFI_MESSAGES[reason],
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def wifi_qr_failure() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail="cannot generate Wi-Fi QR; check the SSID, password and security mode",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
