"""Yoosee BLE response semantics and secret-safe public projection."""

from __future__ import annotations

import hmac
import json
import logging

from ..onboarding import BleDecodeResult, OnboardingInputError
from .ble import (
    BleCodecError,
    ble_provisioning_attempt,
    decrypt_ble_payload,
)
from .ble_public_payload import network_metadata
from .privileged import PrivilegedEnrollmentError, remember_privileged_handoff

log = logging.getLogger(__name__)
_RESPONSE_COMMANDS = {0x71, 0x73, 0x81, 0x83, 0x85}


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
    except BleCodecError as exc:
        raise OnboardingInputError("BLE response could not be decoded", reason=exc.reason) from None
    except ValueError:
        raise OnboardingInputError("BLE response could not be decoded") from None

    text = ""
    payload = None
    try:
        text = decoded.rstrip(b"\x00").decode("utf-8")
        payload = json.loads(text) if text else None
    except (UnicodeDecodeError, ValueError, RecursionError):
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
    wifi_connection: dict[str, object] | None = None
    public_payload = network_metadata(command, payload)
    connect_status = None
    handoff_advertised = False
    if command == 0x85:
        # Project a known scalar, never a dictionary minus a secret denylist.
        source = payload if isinstance(payload, dict) else {}
        confirm_key = source.get("confirmKey")
        candidate = source.get("connectStatus")
        if type(candidate) is int and -(2**31) <= candidate < 2**31:
            connect_status = candidate
        public_payload = {"connectStatus": connect_status} if connect_status is not None else None
        handoff_advertised = isinstance(confirm_key, str) and bool(confirm_key)
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
            "privileged_handoff_advertised": handoff_advertised,
            "privileged_handoff_ready": handoff_ready,
        }
    # Challenge/echo data are never public; 0x85 only exposes its validated status.
    if command in {0x71, 0x83}:
        text = ""
        public_payload = None
    text = (
        json.dumps(public_payload, separators=(",", ":"), ensure_ascii=False)
        if public_payload is not None
        else ""
    )
    log.warning(
        "BLE response device=%s command=0x%02x bytes=%d encrypted=%d text=%d "
        "connect_status=%s privileged_handoff=%d",
        device_id,
        command,
        len(decoded),
        int(encrypted),
        int(bool(text)),
        connect_status,
        int(handoff_advertised),
    )
    return BleDecodeResult(
        command=command,
        encrypted=encrypted,
        length=len(decoded),
        valid=challenge_valid,
        text=text[:4096],
        public_payload=public_payload,
        hex_preview="",
        configuration_acknowledged=command == 0x83,
        wifi_connection=wifi_connection,
    )
