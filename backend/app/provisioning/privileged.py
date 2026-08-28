"""Explicit post-Wi-Fi enrollment for Yoosee/Gwell T-devices.

Bluetooth provisioning and privileged enrollment are deliberately separate operations.  The APK
races two confirmations after sending Wi-Fi data: asynchronous BLE command ``0x85`` can carry a
``confirmKey``, while ``cloud/netcfg/devresult`` confirms the same ``configToken`` without one.
Either result may authorize a later, explicit account bind; merely receiving ``0x83`` never does.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..db import p2p
from ..drivers.yoosee.ble import BleProvisioningMaterial

_HOST = "openapi-iot.cloudlinks.cn"
_BIND_PATH = "/openapi/app/user/device/bind"
_ONLINE_STATUS_PATH = "/openapi/netcfg/cloud/netcfg/devresult"
_INTEGER_BODY_NAMES = {"terminalOS", "apiVersion", "platform", "accessId", "funcSupport"}
_HANDOFF_TTL_SECONDS = 180


class PrivilegedEnrollmentError(RuntimeError):
    """Sanitized enrollment failure safe to return through the local API."""


@dataclass(frozen=True, slots=True)
class PendingEnrollment:
    device_id: str
    confirm_key: str | None
    material: BleProvisioningMaterial
    expires_at: float


@dataclass(frozen=True, slots=True)
class VendorBindResult:
    success: bool
    code: int | None
    message: str
    dev_token: str | None


@dataclass(frozen=True, slots=True)
class VendorOnlineResult:
    success: bool
    code: int | None
    message: str
    online: bool
    terminal_failure: bool
    device_id: str | None


@dataclass(frozen=True, slots=True)
class BoundEnrollment:
    device_id: str
    dev_token: str
    bound_at: float


_lock = threading.Lock()
_pending: dict[str, PendingEnrollment] = {}
_bound: dict[str, BoundEnrollment] = {}


def _now() -> float:
    return time.time()


def _purge_expired(now: float) -> None:
    expired = [device_id for device_id, item in _pending.items() if item.expires_at <= now]
    for device_id in expired:
        _pending.pop(device_id, None)


def remember_privileged_handoff(
    material: BleProvisioningMaterial,
    *,
    confirm_key: str | None,
    now: float | None = None,
) -> None:
    """Retain a BLE or cloud-confirmed handoff without binding or exposing secrets."""
    proof = None if confirm_key is None else str(confirm_key)
    if proof is not None and (not proof or len(proof) > 4096):
        raise PrivilegedEnrollmentError("camera enrollment proof is invalid")
    current = _now() if now is None else float(now)
    # Either a fresh confirmKey or a successful devresult lookup proves that the exact configToken
    # was accepted by the camera. Start the explicit-bind window at that confirmation.
    expires_at = current + _HANDOFF_TTL_SECONDS
    item = PendingEnrollment(material.device_id, proof, material, expires_at)
    with _lock:
        _purge_expired(current)
        _pending[material.device_id] = item
        _bound.pop(material.device_id, None)


def privileged_enrollment_status(device_id: str, *, now: float | None = None) -> dict[str, object]:
    current = _now() if now is None else float(now)
    with _lock:
        _purge_expired(current)
        pending = _pending.get(str(device_id))
        bound = _bound.get(str(device_id))
    durable = p2p.get_enrollment(str(device_id))
    return {
        "device_id": str(device_id),
        "handoff_ready": pending is not None,
        "expires_in": max(0, int(pending.expires_at - current)) if pending else 0,
        "bound": bound is not None or bool(durable and durable.dev_token),
        "subscription_material_ready": bool(
            (bound and bound.dev_token) or (durable and durable.dev_token)
        ),
        "p2p_access_ready": durable is not None,
        "rtsp_ready": False,
    }


def pending_privileged_enrollment(device_id: str, *, now: float | None = None) -> PendingEnrollment:
    current = _now() if now is None else float(now)
    with _lock:
        _purge_expired(current)
        item = _pending.get(str(device_id))
    if item is None:
        raise PrivilegedEnrollmentError(
            "no fresh post-Wi-Fi enrollment handoff is available; repeat Bluetooth provisioning"
        )
    return item


def mark_privileged_enrollment_bound(
    item: PendingEnrollment, dev_token: str, *, camera_id: str | None = None
) -> None:
    token = str(dev_token)
    if not token:
        raise PrivilegedEnrollmentError("vendor binding returned no P2P subscription material")
    material = item.material
    if material.cloud_access_token is None or material.cloud_headers is None:
        raise PrivilegedEnrollmentError("authenticated P2P material is unavailable")
    try:
        signed_access_id = int(material.cloud_headers["x-iotvideo-accessid"])
        access_id = signed_access_id & 0xFFFFFFFFFFFFFFFF
    except (KeyError, TypeError, ValueError) as exc:
        raise PrivilegedEnrollmentError("authenticated P2P identity is unavailable") from exc
    with _lock:
        current = _pending.get(item.device_id)
        if current is not item:
            raise PrivilegedEnrollmentError("camera enrollment handoff changed or expired")
        try:
            # Persist before consuming the one-time handoff. A failed disk/validation operation
            # leaves the handoff intact so the caller can retry instead of losing the only token.
            p2p.upsert_enrollment(
                item.device_id,
                access_id=access_id,
                access_token=material.cloud_access_token,
                dev_token=token,
                camera_id=camera_id,
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise PrivilegedEnrollmentError(
                "P2P subscription material could not be stored securely"
            ) from exc
        _pending.pop(item.device_id, None)
        _bound[item.device_id] = BoundEnrollment(item.device_id, token, _now())


def bound_privileged_enrollment(device_id: str) -> p2p.P2PEnrollment:
    """Load durable P2P credentials for backend-only transport initialization."""
    enrollment = p2p.get_enrollment(str(device_id))
    if enrollment is None:
        raise PrivilegedEnrollmentError("durable P2P subscription material is unavailable")
    return enrollment


def bound_privileged_enrollment_for_camera(camera_id: str) -> p2p.P2PEnrollment:
    """Resolve P2P credentials through the dashboard's authoritative camera identity."""

    try:
        enrollment = p2p.get_enrollment_for_camera(camera_id)
    except ValueError as exc:
        raise PrivilegedEnrollmentError("camera identity association is invalid") from exc
    if enrollment is None:
        raise PrivilegedEnrollmentError("camera has no linked P2P enrollment")
    return enrollment


def _signature(fields: dict[str, str], access_token: bytes) -> str:
    content = "\n".join(f"{key}:{fields[key]}" for key in sorted(fields))
    digest = hmac.new(access_token[48:64], content.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _bind_body(
    item: PendingEnrollment,
    *,
    time_area: str,
    time_zone: int,
) -> bytes:
    material = item.material
    if material.cloud_access_token is None or material.cloud_common is None:
        raise PrivilegedEnrollmentError("authenticated enrollment material is unavailable")
    data: dict[str, object] = dict(material.cloud_common)
    data["accessToken"] = material.cloud_access_token[:48].hex()
    for name in _INTEGER_BODY_NAMES:
        if name in data:
            data[name] = int(str(data[name]))
    data.update(
        {
            "devId": item.device_id,
            "tid": "",
            "remarkName": item.device_id,
            "permission": 3,
            "bindToken": material.config_token,
            "devType": 0,
            "timeArea": time_area,
            "timeZone": int(time_zone),
            "latitude": "",
            "longitude": "",
            "linkType": 1,
        }
    )
    # HttpServiceAdapter uses a default Gson instance. Gson omits null map values, so the APK's
    # cloud-online fallback sends no confirmKey field at all rather than JSON null or an empty value.
    if item.confirm_key is not None:
        data["confirmKey"] = item.confirm_key
    return json.dumps(data, separators=(",", ":")).encode()


def _authenticated_body(material: BleProvisioningMaterial) -> dict[str, object]:
    if material.cloud_access_token is None or material.cloud_common is None:
        raise PrivilegedEnrollmentError("authenticated enrollment material is unavailable")
    data: dict[str, object] = dict(material.cloud_common)
    data["accessToken"] = material.cloud_access_token[:48].hex()
    for name in _INTEGER_BODY_NAMES:
        if name in data:
            data[name] = int(str(data[name]))
    return data


def _signed_post(
    material: BleProvisioningMaterial,
    *,
    path: str,
    body: bytes,
    timeout: float,
) -> tuple[int, dict[str, object]]:
    headers_template = material.cloud_headers
    access_token = material.cloud_access_token
    if headers_template is None or access_token is None:
        raise PrivilegedEnrollmentError("authenticated enrollment material is unavailable")
    nonce = str(secrets.randbelow(2_147_483_647) + 1)
    timestamp = str(int(_now()))
    try:
        access_id = headers_template["x-iotvideo-accessid"]
    except KeyError as exc:
        raise PrivilegedEnrollmentError("authenticated enrollment identity is unavailable") from exc
    fields = {
        "host": _HOST,
        "payload": hashlib.sha256(body).hexdigest(),
        "x-iotvideo-accessid": access_id,
        "x-iotvideo-nonce": nonce,
        "x-iotvideo-timestamp": timestamp,
    }
    headers = {
        "x-iotvideo-accessid": access_id,
        "x-iotvideo-nonce": nonce,
        "x-iotvideo-timestamp": timestamp,
        "x-iotvideo-area": headers_template.get("x-iotvideo-area", ""),
        "x-iotvideo-appver": headers_template.get("x-iotvideo-appver", ""),
        "x-iotvideo-appid": headers_template.get("x-iotvideo-appid", ""),
        "x-iotvideo-uniqueid": headers_template.get("x-iotvideo-uniqueid", ""),
        "x-iotvideo-signature": _signature(fields, access_token),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = Request(f"https://{_HOST}{path}", data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    except (OSError, URLError) as exc:
        raise PrivilegedEnrollmentError("vendor enrollment service is unavailable") from exc
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivilegedEnrollmentError(
            "vendor enrollment service returned an invalid response"
        ) from exc
    if not isinstance(payload, dict):
        raise PrivilegedEnrollmentError("vendor enrollment service returned an invalid response")
    return status, payload


def query_vendor_device_online(
    material: BleProvisioningMaterial,
    *,
    timeout: float = 15,
) -> VendorOnlineResult:
    """Mirror ``ConfigNetOnlineStatusProxy.queryDevOnlineStatus`` without mutating the camera."""
    data = _authenticated_body(material)
    data["token"] = material.config_token
    status, payload = _signed_post(
        material,
        path=_ONLINE_STATUS_PATH,
        body=json.dumps(data, separators=(",", ":")).encode(),
        timeout=timeout,
    )
    raw_code = payload.get("code")
    code = (
        int(raw_code)
        if isinstance(raw_code, int | str) and str(raw_code).lstrip("-").isdigit()
        else None
    )
    raw_message = payload.get("msg")
    message = raw_message[:256] if isinstance(raw_message, str) else ""
    response_data = payload.get("data")
    result_status = response_data.get("status") if isinstance(response_data, dict) else None
    if isinstance(result_status, str) and result_status.lstrip("-").isdigit():
        result_status = int(result_status)
    device_id = response_data.get("devId") if isinstance(response_data, dict) else None
    if not isinstance(device_id, str):
        device_id = None
    request_succeeded = status == 200 and code == 0 and isinstance(response_data, dict)
    return VendorOnlineResult(
        success=request_succeeded,
        code=code,
        message=message,
        online=request_succeeded and result_status == 1,
        terminal_failure=request_succeeded and result_status == 0,
        device_id=device_id,
    )


def bind_vendor_device(
    item: PendingEnrollment,
    *,
    time_area: str,
    time_zone: int,
    timeout: float = 15,
) -> VendorBindResult:
    """Perform the explicit account bind recovered from ``WaitDeviceOnlineVM.bindTDevice``."""
    material = item.material
    body = _bind_body(item, time_area=time_area, time_zone=time_zone)
    status, payload = _signed_post(material, path=_BIND_PATH, body=body, timeout=timeout)
    code = payload.get("code")
    code = int(code) if isinstance(code, int | str) and str(code).lstrip("-").isdigit() else None
    raw_message = payload.get("msg")
    message = raw_message if isinstance(raw_message, str) else ""
    data = payload.get("data")
    dev_token = data.get("devToken") if isinstance(data, dict) else None
    if not isinstance(dev_token, str):
        dev_token = None
    return VendorBindResult(status == 200 and code == 0, code, message[:256], dev_token)


def _clear_privileged_enrollments_for_tests() -> None:
    with _lock:
        _pending.clear()
        _bound.clear()
