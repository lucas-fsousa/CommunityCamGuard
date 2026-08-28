"""Factory-new camera provisioning HTTP workflow."""

from __future__ import annotations

import base64
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, SecretStr

from .. import drivers
from ..auth import require_auth
from ..camera_identity import stable_camera_id
from ..config import get_settings
from ..drivers.onboarding import (
    AccountLogin,
    OnboardingAccountError,
    OnboardingTransportError,
)
from ..provisioning import (
    BleCodecError,
    LabelError,
    PrivilegedEnrollmentError,
    VendorProvisioningCloudError,
    WifiSelectionError,
    begin_ble_provisioning_attempt,
    bind_vendor_device,
    ble_provisioning_attempt,
    build_ble_provisioning_frames,
    build_wifi_payload,
    decrypt_ble_payload,
    encryption_from_scan,
    inspect_label,
    manual_network,
    mark_privileged_enrollment_bound,
    pending_privileged_enrollment,
    privileged_enrollment_status,
    query_vendor_device_online,
    remember_privileged_handoff,
    render_svg_base64,
    scan_wifi_networks,
    selected_network,
)
from .local_only import require_local_or_remote_ble_request, require_local_request

router = APIRouter(prefix="/api", tags=["provisioning"])
log = logging.getLogger(__name__)


def _onboarding():
    """Resolve onboarding behavior through the registered camera driver."""

    return drivers.onboarding_provider()


# --- schemas -----------------------------------------------------------------------


class ProvisioningLabelIn(BaseModel):
    """Identity visible on a factory-new camera; none of these fields are credentials."""

    label: str = Field(default="", max_length=512)
    device_id: str = Field(default="", max_length=20)
    capability_code: str = Field(default="", max_length=10)
    firmware_version: str = Field(default="", max_length=64)
    mac: str = Field(default="", max_length=32)


class ProvisioningStartIn(ProvisioningLabelIn):
    """Ephemeral setup request. ``wifi_password`` must never be persisted or logged."""

    wifi_network_id: str = Field(min_length=1, max_length=1024)
    wifi_password: SecretStr = Field(default=SecretStr(""), max_length=128)


class ProvisioningManualNetworkIn(BaseModel):
    """Explicit fallback used only when this server has no usable Wi-Fi scanner."""

    ssid: str = Field(min_length=1, max_length=64)
    security: str = Field(default="wpa", max_length=16)


class ProvisioningBleResponseIn(ProvisioningLabelIn):
    """One camera response returned by Web Bluetooth for ephemeral server-side decoding."""

    attempt_id: str = Field(min_length=20, max_length=128)
    command: int = Field(ge=0, le=255)
    encrypted: bool = False
    data_base64: str = Field(default="", max_length=16384)
    time_area: str = Field(default="UTC", min_length=1, max_length=128)
    time_zone: int = Field(default=0, ge=-50_400, le=50_400)


class ProvisioningPrivilegedBindIn(ProvisioningLabelIn):
    """Explicit second onboarding stage, after Wi-Fi has already been configured."""

    time_area: str = Field(default="UTC", min_length=1, max_length=128)
    time_zone: int = Field(default=0, ge=-50_400, le=50_400)


class ProvisioningOnlineStatusIn(ProvisioningLabelIn):
    """Read-only APK-compatible lookup for the configToken pinned to one BLE attempt."""

    attempt_id: str = Field(min_length=20, max_length=128)


class ProvisioningP2PPropertyReadIn(ProvisioningLabelIn):
    """One allowlisted thing-model read for the explicitly identified camera."""

    property_path: str = Field(min_length=1, max_length=128)


class ProvisioningVendorAccountLoginIn(BaseModel):
    """Vendor credentials accepted only by the authenticated local-network route."""

    account_type: str = Field(pattern="^(email|mobile|userId)$")
    account: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=256)
    mobile_area: str = Field(default="0", max_length=16)
    language: str = Field(default="en", max_length=16)
    region: str = Field(default="US", max_length=16)
    area: str = Field(default="us", max_length=16)


# --- factory-new provisioning (strictly localhost-only) -----------------------------

_LOCAL_PROVISIONING = [Depends(require_auth), Depends(require_local_request)]
_BLE_PROVISIONING = [Depends(require_auth), Depends(require_local_or_remote_ble_request)]


def _inspect_provisioning_label(body: ProvisioningLabelIn) -> dict:
    try:
        return inspect_label(
            label=body.label,
            device_id=body.device_id,
            capability_code=body.capability_code,
            firmware_version=body.firmware_version,
            mac=body.mac,
        )
    except LabelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/provisioning/status", dependencies=_BLE_PROVISIONING)
def provisioning_status() -> dict:
    """Describe the local onboarding surface without probing or changing any camera."""
    material_path = get_settings().provisioning_ble_material_file
    native_account = _onboarding().account_configured()
    ble_status = (
        "handshake-ready"
        if native_account or (material_path and material_path.is_file())
        else "discovery-ready"
    )
    return {
        "local_only": False,
        "lan_only": True,
        "remote_ble_enabled": get_settings().provisioning_remote_ble_enabled,
        "label_inspection": True,
        "transport_ready": True,
        "vendor_cloud_required": True,
        "vendor_account_configured": native_account,
        "ble_material_source": (
            "native-account"
            if native_account
            else "research-file"
            if material_path and material_path.is_file()
            else "none"
        ),
        "transports": {
            "qr": "experimental-ready",
            "softap": "protocol-recovery",
            "bluetooth": ble_status,
            "wired": "planned",
        },
    }


@router.get(
    "/provisioning/vendor-account/status",
    dependencies=_LOCAL_PROVISIONING,
)
def provisioning_vendor_account_status(response: Response) -> dict:
    """Report enrollment state without disclosing an account identity or token."""

    onboarding = _onboarding()
    configured = onboarding.account_configured()
    response.headers["Cache-Control"] = "no-store"
    return {
        "provider": onboarding.provider,
        "configured": configured,
        "renewable_session": configured,
        "vendor_cloud_required": True,
    }


@router.post(
    "/provisioning/vendor-account/login",
    dependencies=_LOCAL_PROVISIONING,
)
def provisioning_vendor_account_login(
    body: ProvisioningVendorAccountLoginIn,
    response: Response,
) -> dict:
    """Establish and encrypt a renewable native session; Android/Frida are not involved."""

    try:
        onboarding = _onboarding()
        onboarding.login(
            AccountLogin(
                account_type=body.account_type,
                account=body.account.strip(),
                password=body.password.get_secret_value(),
                mobile_area=body.mobile_area,
                language=body.language,
                region=body.region,
                area=body.area,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OnboardingAccountError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "provider": onboarding.provider,
        "configured": True,
        "renewable_session": True,
    }


@router.post(
    "/provisioning/vendor-account/refresh",
    dependencies=_LOCAL_PROVISIONING,
)
def provisioning_vendor_account_refresh(response: Response) -> dict:
    """Renew the encrypted native session without returning any credential material."""

    onboarding = _onboarding()
    try:
        onboarding.refresh_account()
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingAccountError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {
        "provider": onboarding.provider,
        "configured": True,
        "renewable_session": True,
        "refreshed": True,
    }


@router.post("/provisioning/inspect", dependencies=_BLE_PROVISIONING)
def provisioning_inspect(body: ProvisioningLabelIn) -> dict:
    """Validate and decode a scanned/typed factory label without contacting the camera."""
    return _inspect_provisioning_label(body)


@router.get("/provisioning/networks", dependencies=_BLE_PROVISIONING)
def provisioning_networks(response: Response) -> dict:
    """Read-only scan from the server's Wi-Fi radio; SSIDs carry short-lived signed IDs."""
    networks, scanner, error = scan_wifi_networks()
    response.headers["Cache-Control"] = "no-store"
    return {
        "networks": [network.public() for network in networks],
        "scanner": scanner,
        "error": error or None,
        "manual_entry_allowed": not scanner,
    }


@router.post("/provisioning/networks/manual", dependencies=_BLE_PROVISIONING)
def provisioning_manual_network(body: ProvisioningManualNetworkIn, response: Response) -> dict:
    """Sign an explicit SSID only when automatic Wi-Fi discovery is unavailable."""
    _networks, scanner, _error = scan_wifi_networks()
    if scanner:
        raise HTTPException(
            status_code=409,
            detail="manual Wi-Fi entry is disabled while automatic scanning is available",
        )
    try:
        network = manual_network(body.ssid, body.security)
    except WifiSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"network": network.public()}


@router.post("/provisioning/start", dependencies=_LOCAL_PROVISIONING)
def provisioning_start(body: ProvisioningStartIn, response: Response) -> dict:
    """Create the recovered vendor Wi-Fi QR without persisting its embedded credentials.

    Rendering the artifact is not proof that the camera read or accepted it, so this deliberately
    returns an ``awaiting_camera_scan`` state rather than claiming successful provisioning.
    """
    identity = _inspect_provisioning_label(body)
    if "qr" not in identity["setup_modes"]:
        raise HTTPException(
            status_code=501,
            detail="camera label does not advertise QR provisioning; SoftAP is not ready yet",
        )
    try:
        network = selected_network(body.wifi_network_id)
    except WifiSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The secret exists only for this synchronous render. Do not log, return or persist either the
    # plain password or the textual QR payload (which contains it by construction).
    password = body.wifi_password.get_secret_value()
    try:
        payload = build_wifi_payload(
            ssid=network.ssid,
            password=password,
            encryption=encryption_from_scan(network.security, password),
        )
        qr_data = render_svg_base64(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "status": "awaiting_camera_scan",
        "transport": "qr",
        "experimental": True,
        "cloud_token_used": False,
        "device_id": identity["device_id"],
        "qr": {"mime_type": "image/svg+xml", "data_base64": qr_data},
    }


@router.post("/provisioning/ble/prepare", dependencies=_BLE_PROVISIONING)
def provisioning_ble_prepare(body: ProvisioningStartIn, response: Response) -> dict:
    """Prepare encrypted GATT writes while keeping cloud material and Wi-Fi plaintext server-side."""
    identity = _inspect_provisioning_label(body)
    if "bluetooth" not in identity["setup_modes"]:
        raise HTTPException(
            status_code=422, detail="camera label does not advertise Bluetooth setup"
        )
    try:
        network = selected_network(body.wifi_network_id)
    except WifiSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = get_settings()
    try:
        material = _onboarding().ble_material(
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
        # The vendor Android client explicitly negotiates MTU 256 before initializing its native
        # packet session. At MTU 23 this firmware treats each fragment as a separate command and
        # echoed only the final 13 bytes of the 32-byte challenge, so TanKey was never installed.
        stages = build_ble_provisioning_frames(material, wifi_payload=wifi_payload, mtu=256)
        attempt = begin_ble_provisioning_attempt(material)
    except HTTPException:
        raise
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


@router.post("/provisioning/ble/decode-response", dependencies=_BLE_PROVISIONING)
def provisioning_ble_decode_response(body: ProvisioningBleResponseIn, response: Response) -> dict:
    """Decode a transient camera reply without exposing TanKey or persisting its contents."""
    identity = _inspect_provisioning_label(body)
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
        # Firmware 40.1.x acknowledges the random challenge with only its trailing bytes (13 on
        # the observed device), while other revisions return a JSON DevBleInfo object. The vendor
        # app tolerates both. Verify the short echo server-side without exposing randNumber.
        challenge_valid = (
            bool(decoded)
            and len(decoded) <= len(material.random_number)
            and hmac.compare_digest(
                decoded, material.random_number.encode("utf-8")[-len(decoded) :]
            )
        )
    wifi_connection = None
    configuration_acknowledged = body.command == 0x83
    public_payload = payload
    if body.command == 0x85 and isinstance(payload, dict):
        # DevBleConnWiFiRes.CONNECTED == 0.  This endpoint belongs to the Wi-Fi transport only:
        # account/P2P binding and RTSP activation are separate onboarding stages.  Keep the
        # one-time proof out of the browser response and, importantly, do not consume it here by
        # silently calling the vendor cloud.
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
                # Wi-Fi success remains valid even if its optional, short-lived continuation can no
                # longer be retained. The explicit next stage will report that it must be repeated.
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
    # 0x71 contains handshake material and 0x83 echoes the complete plaintext provisioning
    # payload after server-side decryption, including the Wi-Fi password. Neither may cross back
    # into the browser or remain in an HTTP inspector. The command itself is sufficient as ACK.
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


@router.post(
    "/provisioning/privileged/status",
    dependencies=_BLE_PROVISIONING,
)
def provisioning_privileged_status(body: ProvisioningLabelIn, response: Response) -> dict:
    """Report whether an unconsumed post-Wi-Fi handoff exists; never return its secrets."""
    identity = _inspect_provisioning_label(body)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return privileged_enrollment_status(identity["device_id"])


@router.post(
    "/provisioning/privileged/online-status",
    dependencies=_BLE_PROVISIONING,
)
def provisioning_privileged_online_status(
    body: ProvisioningOnlineStatusIn, response: Response
) -> dict:
    """Perform the read-only configToken status lookup used by the vendor APK."""
    identity = _inspect_provisioning_label(body)
    try:
        attempt = ble_provisioning_attempt(
            body.attempt_id,
            expected_device_id=identity["device_id"],
        )
        result = query_vendor_device_online(attempt.material)
    except (BleCodecError, PrivilegedEnrollmentError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.device_id is not None and result.device_id != identity["device_id"]:
        raise HTTPException(
            status_code=409, detail="vendor online result belongs to a different camera"
        )
    handoff_ready = False
    if result.online:
        remember_privileged_handoff(attempt.material, confirm_key=None)
        handoff_ready = True
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "device_id": identity["device_id"],
        "query_succeeded": result.success,
        "online": result.online,
        "terminal_failure": result.terminal_failure,
        "code": result.code,
        "privileged_handoff_ready": handoff_ready,
    }


@router.post(
    "/provisioning/privileged/bind",
    dependencies=_BLE_PROVISIONING,
)
def provisioning_privileged_bind(body: ProvisioningPrivilegedBindIn, response: Response) -> dict:
    """Explicitly enroll one Wi-Fi-connected camera in the vendor IoTVideo/P2P device list."""
    identity = _inspect_provisioning_label(body)
    try:
        pending = pending_privileged_enrollment(identity["device_id"])
        result = bind_vendor_device(
            pending,
            time_area=body.time_area,
            time_zone=body.time_zone,
        )
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.success:
        detail = result.message or (
            str(result.code) if result.code is not None else "unknown error"
        )
        raise HTTPException(status_code=409, detail=f"camera P2P enrollment failed: {detail}")
    if not result.dev_token:
        raise HTTPException(
            status_code=502, detail="camera P2P enrollment returned no subscription material"
        )
    try:
        mark_privileged_enrollment_bound(
            pending,
            result.dev_token,
            camera_id=(stable_camera_id("mac", identity["mac"]) if identity["mac"] else None),
        )
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    log.info("Privileged P2P enrollment accepted device=%s", identity["device_id"])
    return {
        "device_id": identity["device_id"],
        "p2p_binding": "bound",
        "subscription_material_ready": True,
        "p2p_session": "pending",
        "rtsp": "pending",
    }


@router.post(
    "/provisioning/privileged/p2p-probe",
    dependencies=_BLE_PROVISIONING,
)
def provisioning_privileged_p2p_probe(body: ProvisioningLabelIn, response: Response) -> dict:
    """Authenticate to the P2P access node and inspect inventory without contacting the camera."""
    identity = _inspect_provisioning_label(body)
    try:
        inventory = _onboarding().probe_inventory(identity["device_id"])
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "device_id": identity["device_id"],
        "authenticated": inventory.authenticated,
        "device_count": inventory.device_count,
        "online_count": inventory.online_count,
        "target_visible": inventory.target_visible,
        "target_online": inventory.target_online,
        "target_term_resolved": inventory.target_term_resolved,
        "skipped_incomplete_nodes": inventory.skipped_incomplete_nodes,
        "camera_contacted": False,
    }


@router.post(
    "/provisioning/privileged/p2p-route-probe",
    dependencies=_BLE_PROVISIONING,
)
def provisioning_privileged_p2p_route_probe(body: ProvisioningLabelIn, response: Response) -> dict:
    """Prove the selected camera's direct P2P route without media or control commands."""
    identity = _inspect_provisioning_label(body)
    try:
        route = _onboarding().probe_route(identity["device_id"])
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "device_id": identity["device_id"],
        "authenticated": route.authenticated,
        "target_visible": route.target_visible,
        "target_online": route.target_online,
        "broker_acknowledged": route.broker_acknowledged,
        "route_advertised": route.route_advertised,
        "direct_datagrams": route.direct_datagrams,
        "direct_handshake": route.direct_handshake,
        "camera_contacted": route.camera_contacted,
        "broker_error_code": route.broker_error_code,
        "media_opened": False,
        "command_sent": False,
    }


@router.post(
    "/provisioning/privileged/p2p-property-read",
    dependencies=_LOCAL_PROVISIONING,
)
def provisioning_privileged_p2p_property_read(
    body: ProvisioningP2PPropertyReadIn, response: Response
) -> dict:
    """Read one allowlisted thing-model property from exactly the requested camera."""
    identity = _inspect_provisioning_label(body)
    onboarding = _onboarding()
    if body.property_path not in onboarding.read_only_property_paths:
        raise HTTPException(status_code=422, detail="thing-model path is not read-only allowlisted")
    try:
        result = onboarding.read_property(identity["device_id"], body.property_path)
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "device_id": identity["device_id"],
        "property_path": result.property_path,
        "authenticated": result.authenticated,
        "direct_handshake": result.direct_handshake,
        "transport_acknowledged": result.transport_acknowledged,
        "error_code": result.error_code,
        "value": result.value,
        "write_capable": False,
        "action_capable": False,
    }
