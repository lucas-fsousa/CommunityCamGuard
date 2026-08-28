"""Yoosee BLE response semantics and secret-safe public projection."""

from __future__ import annotations

import hmac
import json
import logging

from ...provisioning import PrivilegedEnrollmentError, remember_privileged_handoff
from ..onboarding import BleDecodeResult, OnboardingInputError
from .ble import (
    BleCodecError,
    ble_provisioning_attempt,
    decrypt_ble_payload,
)

log = logging.getLogger(__name__)
_RESPONSE_COMMANDS = {0x71, 0x73, 0x81, 0x83, 0x85}
_SECRET_COMMANDS = {0x71, 0x83, 0x85}


def decode_response(
    *,
    device_id: str,
    attempt_id: str,
    command: int,
    encrypted: bool,
    raw: bytes,
) -> BleDecodeResult:
    """Decode one response while ensuring handshake and Wi-Fi secrets never cross the port."""

    if command not in _RESPONSE_COMMANDS:
        raise OnboardingInputError("unsupported BLE provisioning response")
    try:
        attempt = ble_provisioning_attempt(attempt_id, expected_device_id=device_id)
        material = attempt.material
        decoded = decrypt_ble_payload(raw, material.tan_key) if encrypted else raw
    except (BleCodecError, ValueError) as exc:
        raise OnboardingInputError(str(exc)) from exc

    text = ""
    payload = None
    try:
        text = decoded.rstrip(b"\x00").decode("utf-8")
        payload = json.loads(text) if text else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    challenge_valid = None
    if command == 0x71:
        challenge_valid = (
            bool(decoded)
            and len(decoded) <= len(material.random_number)
            and hmac.compare_digest(
                decoded,
                material.random_number.encode("utf-8")[-len(decoded) :],
            )
        )
    wifi_connection = None
    public_payload = payload
    if command == 0x85 and isinstance(payload, dict):
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
                    device_id,
                )
        wifi_connection = {
            "connected": connect_status == 0,
            "status": connect_status,
            "privileged_handoff_advertised": isinstance(confirm_key, str) and bool(confirm_key),
            "privileged_handoff_ready": handoff_ready,
        }
    # Challenge data and the echoed Wi-Fi request are never public. The 0x85 projection excludes
    # confirmKey by construction and is serialized again only from that sanitized mapping.
    if command in {0x71, 0x83}:
        text = ""
        public_payload = None
    if command == 0x85:
        text = (
            json.dumps(public_payload, separators=(",", ":"), ensure_ascii=False)
            if public_payload is not None
            else ""
        )
    log.warning(
        "BLE response device=%s command=0x%02x bytes=%d encrypted=%d text=%d json_keys=%s "
        "connect_status=%s privileged_handoff=%d",
        device_id,
        command,
        len(decoded),
        int(encrypted),
        int(bool(text)),
        sorted(str(key) for key in payload) if isinstance(payload, dict) else [],
        payload.get("connectStatus", "-") if isinstance(payload, dict) else "-",
        int(bool(payload.get("confirmKey"))) if isinstance(payload, dict) else 0,
    )
    return BleDecodeResult(
        command=command,
        encrypted=encrypted,
        length=len(decoded),
        valid=challenge_valid,
        text=text[:4096],
        public_payload=public_payload,
        hex_preview="" if command in _SECRET_COMMANDS else decoded[:128].hex() if not text else "",
        configuration_acknowledged=command == 0x83,
        wifi_connection=wifi_connection,
    )
