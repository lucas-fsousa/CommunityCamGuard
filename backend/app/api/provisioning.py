"""Factory-new camera provisioning HTTP workflow."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response

from ..camera_identity import stable_camera_id
from ..config import get_settings
from ..drivers.onboarding import OnboardingTransportError
from ..provisioning import (
    BleCodecError,
    PrivilegedEnrollmentError,
    bind_vendor_device,
    ble_provisioning_attempt,
    mark_privileged_enrollment_bound,
    pending_privileged_enrollment,
    privileged_enrollment_status,
    query_vendor_device_online,
    remember_privileged_handoff,
)
from .provisioning_common import (
    BLE_PROVISIONING,
    LOCAL_PROVISIONING,
    ProvisioningLabelIn,
    ProvisioningOnlineStatusIn,
    ProvisioningP2PPropertyReadIn,
    ProvisioningPrivilegedBindIn,
    inspect_provisioning_label,
    onboarding,
)

router = APIRouter(prefix="/api", tags=["provisioning"])
log = logging.getLogger(__name__)


# --- factory-new provisioning (strictly localhost-only) -----------------------------

_LOCAL_PROVISIONING = LOCAL_PROVISIONING
_BLE_PROVISIONING = BLE_PROVISIONING
_inspect_provisioning_label = inspect_provisioning_label
_onboarding = onboarding


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
