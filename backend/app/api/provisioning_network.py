"""Wi-Fi discovery, manual selection and QR provisioning endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..provisioning import (
    WifiSelectionError,
    manual_network,
    scan_wifi_networks,
    selected_network,
)
from .provisioning_common import (
    BLE_PROVISIONING,
    LOCAL_PROVISIONING,
    ProvisioningLabelIn,
    ProvisioningManualNetworkIn,
    ProvisioningStartIn,
    inspect_provisioning_label,
    onboarding,
)

router = APIRouter(prefix="/api/provisioning", tags=["provisioning"])


@router.post("/inspect", dependencies=BLE_PROVISIONING)
def provisioning_inspect(body: ProvisioningLabelIn) -> dict:
    """Validate and decode a scanned or typed label without contacting the camera."""

    return inspect_provisioning_label(body)


@router.get("/networks", dependencies=BLE_PROVISIONING)
def provisioning_networks(response: Response) -> dict:
    """Scan the server Wi-Fi radio and return short-lived signed network IDs."""

    networks, scanner, error = scan_wifi_networks()
    response.headers["Cache-Control"] = "no-store"
    return {
        "networks": [network.public() for network in networks],
        "scanner": scanner,
        "error": error or None,
        "manual_entry_allowed": not scanner,
    }


@router.post("/networks/manual", dependencies=BLE_PROVISIONING)
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


@router.post("/start", dependencies=LOCAL_PROVISIONING)
def provisioning_start(body: ProvisioningStartIn, response: Response) -> dict:
    """Create the recovered vendor Wi-Fi QR without retaining plaintext credentials."""

    identity = inspect_provisioning_label(body)
    if "qr" not in identity["setup_modes"]:
        raise HTTPException(
            status_code=501,
            detail="camera label does not advertise QR provisioning; SoftAP is not ready yet",
        )
    try:
        network = selected_network(body.wifi_network_id)
    except WifiSelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    password = body.wifi_password.get_secret_value()
    try:
        provider = onboarding(body.driver) if body.driver else onboarding()
        qr_data = provider.build_wifi_qr(
            ssid=network.ssid,
            password=password,
            security=network.security,
        )
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
