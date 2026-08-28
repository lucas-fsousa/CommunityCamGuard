"""Encrypted Web Bluetooth preparation and response decoding endpoints."""

from __future__ import annotations

import base64
import time

from fastapi import APIRouter, HTTPException, Response

from ..config import get_settings
from ..drivers.onboarding import (
    OnboardingAccountError,
    OnboardingInputError,
    OnboardingTransportError,
)
from ..provisioning import (
    WifiSelectionError,
    selected_network,
)
from .provisioning_common import (
    BLE_PROVISIONING,
    ProvisioningBleResponseIn,
    ProvisioningStartIn,
    inspect_provisioning_label,
    onboarding,
)

router = APIRouter(prefix="/api/provisioning/ble", tags=["provisioning"])


@router.post("/prepare", dependencies=BLE_PROVISIONING)
def provisioning_ble_prepare(body: ProvisioningStartIn, response: Response) -> dict:
    """Prepare encrypted GATT writes while keeping Wi-Fi plaintext server-side."""

    identity = inspect_provisioning_label(body)
    if "bluetooth" not in identity["setup_modes"]:
        raise HTTPException(
            status_code=422,
            detail="camera label does not advertise Bluetooth setup",
        )
    try:
        network = selected_network(body.wifi_network_id)
    except WifiSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = get_settings()
    try:
        prepared = onboarding().prepare_ble(
            device_id=identity["device_id"],
            ssid=network.ssid,
            password=body.wifi_password.get_secret_value(),
            security=network.security,
            fallback_file=settings.provisioning_ble_material_file,
            max_age_seconds=settings.provisioning_ble_material_max_age_seconds,
        )
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OnboardingAccountError, OnboardingTransportError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OnboardingInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "status": "ready_for_explicit_browser_send",
        "transport": "bluetooth",
        "experimental": True,
        "device_id": identity["device_id"],
        "attempt_id": prepared.attempt_id,
        "attempt_expires_in": max(0, int(prepared.expires_at - time.time())),
        "frames": {
            stage: [base64.b64encode(frame).decode("ascii") for frame in frames]
            for stage, frames in prepared.frames.items()
        },
        "expected_responses": {
            "challenge": 0x71,
            "wifi_list": 0x81,
            "wifi_config_ack": 0x83,
            "wifi_connection": 0x85,
        },
    }


@router.post("/decode-response", dependencies=BLE_PROVISIONING)
def provisioning_ble_decode_response(body: ProvisioningBleResponseIn, response: Response) -> dict:
    """Decode a transient camera reply without exposing or persisting secret material."""

    identity = inspect_provisioning_label(body)
    try:
        raw = base64.b64decode(body.data_base64, validate=True) if body.data_base64 else b""
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="invalid BLE response encoding") from exc
    try:
        decoded = onboarding().decode_ble(
            device_id=identity["device_id"],
            attempt_id=body.attempt_id,
            command=body.command,
            encrypted=body.encrypted,
            raw=raw,
        )
    except OnboardingInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "command": decoded.command,
        "encrypted": decoded.encrypted,
        "length": decoded.length,
        "valid": decoded.valid,
        "text": decoded.text,
        "json": decoded.public_payload,
        "hex": decoded.hex_preview,
        "configuration_acknowledged": decoded.configuration_acknowledged,
        "wifi_connection": decoded.wifi_connection,
    }
