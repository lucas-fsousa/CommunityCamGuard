"""Encrypted Web Bluetooth preparation and response decoding endpoints."""

from __future__ import annotations

import base64
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Response

from ..config import get_settings
from ..drivers.onboarding import OnboardingAccountError
from ..provisioning import (
    BleCodecError,
    PrivilegedEnrollmentError,
    VendorProvisioningCloudError,
    WifiSelectionError,
    begin_ble_provisioning_attempt,
    ble_provisioning_attempt,
    build_ble_provisioning_frames,
    build_wifi_payload,
    decrypt_ble_payload,
    encryption_from_scan,
    remember_privileged_handoff,
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
log = logging.getLogger(__name__)


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
        material = onboarding().ble_material(
            identity["device_id"],
            fallback_file=settings.provisioning_ble_material_file,
            max_age_seconds=settings.provisioning_ble_material_max_age_seconds,
        )
        password = body.wifi_password.get_secret_value()
        wifi_payload = build_wifi_payload(
            ssid=network.ssid,
            password=password,
            encryption=encryption_from_scan(network.security, password),
            user_id=material.server_user_id,
            config_token=material.config_token,
        )
        # The recovered client negotiates MTU 256 before initializing its native packet session.
        stages = build_ble_provisioning_frames(material, wifi_payload=wifi_payload, mtu=256)
        attempt = begin_ble_provisioning_attempt(material)
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OnboardingAccountError, VendorProvisioningCloudError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (BleCodecError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "status": "ready_for_explicit_browser_send",
        "transport": "bluetooth",
        "experimental": True,
        "device_id": identity["device_id"],
        "attempt_id": attempt.attempt_id,
        "attempt_expires_in": max(0, int(attempt.expires_at - time.time())),
        "frames": {
            stage: [base64.b64encode(frame).decode("ascii") for frame in frames]
            for stage, frames in stages.items()
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
    if body.command not in {0x71, 0x73, 0x81, 0x83, 0x85}:
        raise HTTPException(status_code=422, detail="unsupported BLE provisioning response")
    try:
        raw = base64.b64decode(body.data_base64, validate=True) if body.data_base64 else b""
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="invalid BLE response encoding") from exc
    try:
        attempt = ble_provisioning_attempt(
            body.attempt_id,
            expected_device_id=identity["device_id"],
        )
        material = attempt.material
        decoded = decrypt_ble_payload(raw, material.tan_key) if body.encrypted else raw
    except (BleCodecError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    text = ""
    payload = None
    try:
        text = decoded.rstrip(b"\x00").decode("utf-8")
        payload = json.loads(text) if text else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    challenge_valid = None
    if body.command == 0x71:
        challenge_valid = (
            bool(decoded)
            and len(decoded) <= len(material.random_number)
            and hmac.compare_digest(
                decoded,
                material.random_number.encode("utf-8")[-len(decoded) :],
            )
        )
    wifi_connection = None
    configuration_acknowledged = body.command == 0x83
    public_payload = payload
    if body.command == 0x85 and isinstance(payload, dict):
        confirm_key = payload.get("confirmKey")
        connect_status = payload.get("connectStatus")
        public_payload = {key: value for key, value in payload.items() if key != "confirmKey"}
        text = json.dumps(public_payload, separators=(",", ":"), ensure_ascii=False)
        handoff_ready = False
        if connect_status == 0 and isinstance(confirm_key, str) and confirm_key:
            try:
                remember_privileged_handoff(material, confirm_key=confirm_key)
                handoff_ready = True
            except PrivilegedEnrollmentError:
                log.warning(
                    "BLE privileged handoff expired before retention device=%s",
                    identity["device_id"],
                )
        wifi_connection = {
            "connected": connect_status == 0,
            "status": connect_status,
            "privileged_handoff_advertised": isinstance(confirm_key, str) and bool(confirm_key),
            "privileged_handoff_ready": handoff_ready,
        }
    # 0x71 is handshake material and 0x83 echoes plaintext provisioning, including the password.
    if body.command in {0x71, 0x83}:
        text = ""
        public_payload = None
    if body.command == 0x85:
        text = (
            json.dumps(public_payload, separators=(",", ":"), ensure_ascii=False)
            if public_payload is not None
            else ""
        )
    log.warning(
        "BLE response device=%s command=0x%02x bytes=%d encrypted=%d text=%d json_keys=%s "
        "connect_status=%s privileged_handoff=%d",
        identity["device_id"],
        body.command,
        len(decoded),
        int(body.encrypted),
        int(bool(text)),
        sorted(str(key) for key in payload) if isinstance(payload, dict) else [],
        payload.get("connectStatus", "-") if isinstance(payload, dict) else "-",
        int(bool(payload.get("confirmKey"))) if isinstance(payload, dict) else 0,
    )
    return {
        "command": body.command,
        "encrypted": body.encrypted,
        "length": len(decoded),
        "valid": challenge_valid,
        "text": text[:4096],
        "json": public_payload,
        "hex": ""
        if body.command in {0x71, 0x83, 0x85}
        else decoded[:128].hex()
        if not text
        else "",
        "configuration_acknowledged": configuration_acknowledged,
        "wifi_connection": wifi_connection,
    }
