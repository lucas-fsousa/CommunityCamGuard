"""Safe public projections; never derive messages from provider exception text."""

from fastapi import HTTPException

from ..drivers.onboarding import OnboardingInputError, OnboardingInputReason
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


_BLE_MESSAGES = {
    OnboardingInputReason.INVALID: "invalid Bluetooth provisioning data; check Wi-Fi settings and retry the Bluetooth step",
    OnboardingInputReason.SESSION_EXPIRED: "BLE provisioning attempt has expired; start the Bluetooth step again",
    OnboardingInputReason.WRONG_CAMERA: "Bluetooth session or material belongs to a different camera; restart setup for this camera",
    OnboardingInputReason.RENEW_MATERIAL: "BLE provisioning material is unavailable or expired; renew material and retry",
    OnboardingInputReason.MATERIAL_PERMISSIONS: "BLE provisioning material must be readable only by its owner; ask the server administrator",
}


def ble_input_failure(error: OnboardingInputError) -> HTTPException:
    reason = error.reason
    if not isinstance(reason, OnboardingInputReason):
        reason = OnboardingInputReason.INVALID
    return HTTPException(
        status_code=422, detail=_BLE_MESSAGES[reason],
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def ble_material_failure(*, unavailable: bool = False) -> HTTPException:
    return HTTPException(
        status_code=503 if unavailable else 502,
        detail=("BLE handshake material is unavailable; configure or refresh the vendor account"
                if unavailable else "BLE material retrieval failed; check the vendor account and retry"),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
