"""Post-Wi-Fi enrollment and bounded read-only P2P verification endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response

from ..camera_identity import stable_camera_id
from ..drivers.onboarding import OnboardingStateError, OnboardingTransportError
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

router = APIRouter(prefix="/api/provisioning/privileged", tags=["provisioning"])
log = logging.getLogger(__name__)


@router.post("/status", dependencies=BLE_PROVISIONING)
def provisioning_privileged_status(body: ProvisioningLabelIn, response: Response) -> dict:
    """Report whether an unconsumed post-Wi-Fi handoff exists without returning secrets."""

    identity = inspect_provisioning_label(body)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    provider = onboarding(body.driver) if body.driver else onboarding()
    return provider.privileged_status(identity["device_id"])


@router.post("/online-status", dependencies=BLE_PROVISIONING)
def provisioning_privileged_online_status(
    body: ProvisioningOnlineStatusIn,
    response: Response,
) -> dict:
    """Perform the read-only config-token status lookup recovered from the vendor client."""

    identity = inspect_provisioning_label(body)
    try:
        provider = onboarding(body.driver) if body.driver else onboarding()
        result = provider.online_status(
            device_id=identity["device_id"],
            attempt_id=body.attempt_id,
        )
    except OnboardingStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "device_id": identity["device_id"],
        "query_succeeded": result.query_succeeded,
        "online": result.online,
        "terminal_failure": result.terminal_failure,
        "code": result.code,
        "privileged_handoff_ready": result.handoff_ready,
    }


@router.post("/bind", dependencies=BLE_PROVISIONING)
def provisioning_privileged_bind(body: ProvisioningPrivilegedBindIn, response: Response) -> dict:
    """Enroll one Wi-Fi-connected camera in its driver's privileged device list."""

    identity = inspect_provisioning_label(body)
    try:
        provider = onboarding(body.driver) if body.driver else onboarding()
        provider.bind(
            device_id=identity["device_id"],
            time_area=body.time_area,
            time_zone=body.time_zone,
            camera_id=(stable_camera_id("mac", identity["mac"]) if identity["mac"] else None),
        )
    except OnboardingStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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


@router.post("/p2p-probe", dependencies=BLE_PROVISIONING)
def provisioning_privileged_p2p_probe(body: ProvisioningLabelIn, response: Response) -> dict:
    """Inspect account inventory without opening camera media or sending a command."""

    identity = inspect_provisioning_label(body)
    try:
        provider = onboarding(body.driver) if body.driver else onboarding()
        inventory = provider.probe_inventory(identity["device_id"])
    except OnboardingStateError as exc:
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


@router.post("/p2p-route-probe", dependencies=BLE_PROVISIONING)
def provisioning_privileged_p2p_route_probe(
    body: ProvisioningLabelIn,
    response: Response,
) -> dict:
    """Prove a direct P2P route without opening media or sending a camera command."""

    identity = inspect_provisioning_label(body)
    try:
        provider = onboarding(body.driver) if body.driver else onboarding()
        route = provider.probe_route(identity["device_id"])
    except OnboardingStateError as exc:
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


@router.post("/p2p-property-read", dependencies=LOCAL_PROVISIONING)
def provisioning_privileged_p2p_property_read(
    body: ProvisioningP2PPropertyReadIn,
    response: Response,
) -> dict:
    """Read one allowlisted property from exactly the requested camera."""

    identity = inspect_provisioning_label(body)
    provider = onboarding(body.driver) if body.driver else onboarding()
    if body.property_path not in provider.read_only_property_paths:
        raise HTTPException(status_code=422, detail="thing-model path is not read-only allowlisted")
    try:
        result = provider.read_property(identity["device_id"], body.property_path)
    except OnboardingStateError as exc:
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
