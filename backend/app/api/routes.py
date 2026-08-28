"""REST API for the dashboard.

Endpoints are split into: auth (login/logout, public), and the protected surface —
cameras (CRUD), discovery scan, media/stream info, storage status, and the recording
timeline. Everything but login requires the session cookie (see :mod:`..auth`).

Mutating the camera set (add/delete) reconfigures the live services: go2rtc gets a fresh
config and the recorder is re-synced to the new camera list.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from .. import drivers
from ..auth import (
    COOKIE_NAME,
    MAX_AGE,
    check_key,
    is_authenticated,
    issue_token,
    require_auth,
)
from ..camera_identity import stable_camera_id
from ..config import get_settings
from ..db import registry
from ..discovery import active_scan, rtsp
from ..drivers.yoosee import account_store
from ..drivers.yoosee.p2p import (
    MODEL_READ_PATHS,
    AccountCredentials,
    P2PProbeError,
    VendorAccountError,
    login_account,
    probe_account_inventory,
    probe_camera_route,
    read_camera_property,
    refresh_account_session,
)
from ..media import go2rtc
from ..provisioning import (
    BleCodecError,
    LabelError,
    PrivilegedEnrollmentError,
    VendorProvisioningCloudError,
    WifiSelectionError,
    begin_ble_provisioning_attempt,
    bind_vendor_device,
    ble_provisioning_attempt,
    bound_privileged_enrollment,
    build_ble_provisioning_frames,
    build_wifi_payload,
    decrypt_ble_payload,
    encryption_from_scan,
    fetch_native_ble_material,
    inspect_label,
    load_ble_provisioning_material,
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
from ..recording import recorder
from ..services import control_catalog
from ..services.camera_runtime import resolve_camera, resync_services
from .local_only import require_local_or_remote_ble_request, require_local_request

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)


# --- schemas -----------------------------------------------------------------------


class LoginIn(BaseModel):
    key: str


class CameraIn(BaseModel):
    mac: str
    name: str | None = None
    username: str | None = None
    password: str | None = None
    stream_path: str | None = None
    rtsp_port: int | None = None
    last_ip: str | None = None
    vendor: str | None = None


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


class PtzIn(BaseModel):
    direction: str | None = None  # up | down | left | right (not needed for stop)
    action: str = "step"  # "start" (hold) | "stop" (release) | "step" (one nudge)


def _camera_out(cam: registry.Camera) -> dict:
    """Registry camera as JSON, without leaking the stored password."""
    controls = control_catalog(cam)
    return {
        "id": cam.camera_id,
        "mac": cam.mac,
        "name": cam.name,
        "username": cam.username,
        "has_password": bool(cam.password),
        "stream_path": cam.stream_path,
        "rtsp_port": cam.rtsp_port,
        "last_ip": cam.last_ip,
        "vendor": cam.vendor,
        "capabilities": cam.capabilities,
        "has_audio": bool(cam.capabilities.get("has_audio")),
        "stream_id": go2rtc.stream_id(cam.camera_id),
        "web_stream_id": go2rtc.web_stream_id(cam.camera_id),
        "hd_stream_id": go2rtc.hd_stream_id(cam.camera_id),
        "has_substream": cam.substream_url is not None,
        # HD and SD are now server-local variants for every camera. This is separate from the
        # vendor camera advertising `/onvif2`, which we intentionally do not open concurrently.
        "has_quality_variants": True,
        "controls": controls,
        # Compatibility alias for older dashboard/API consumers. The driver catalog above is the
        # authoritative source; remove this after clients have migrated to ``controls``.
        "vendor_controls": {key: True for key in controls},
        "webrtc_url": go2rtc.webrtc_page_url(cam.camera_id),
        "recording": False,  # live flag filled in by list_cameras()
        "online": False,  # live RTSP packet progress filled in by list_cameras()
    }


def _camera_runtime_statuses(request: Request, cameras: list[registry.Camera]) -> list[dict]:
    """Read media activity once and map it to the configured camera identities."""

    media = getattr(request.app.state, "media", None)
    rec = getattr(request.app.state, "rec", None)
    try:
        online_probe = getattr(media, "stream_online", None)
        online_streams = online_probe() if callable(online_probe) else {}
    except (OSError, ValueError):
        online_streams = {}
    return [
        {
            "id": cam.camera_id,
            "mac": cam.mac,
            "online": bool(online_streams.get(go2rtc.stream_id(cam.camera_id), False)),
            "recording": bool(rec and rec.is_recording(cam.camera_id)),
        }
        for cam in cameras
    ]


def _resync(request: Request) -> None:
    """Apply registry changes to the running services (go2rtc + recorder).

    Best-effort: the registry write has already succeeded by the time we get here, so a hiccup
    reconfiguring the live services must not fail the API call (it used to 500 the whole add/
    delete). We log and move on; the next scan/restart reconciles.
    """
    resync_services(request)


# --- auth ---------------------------------------------------------------------------


@router.post("/login")
def login(body: LoginIn, response: Response) -> dict:
    if not check_key(body.key):
        raise HTTPException(status_code=401, detail="Invalid key")
    response.set_cookie(COOKIE_NAME, issue_token(), httponly=True, samesite="lax", max_age=MAX_AGE)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    return {"authenticated": is_authenticated(request)}


# --- cameras ------------------------------------------------------------------------


@router.get("/cameras", dependencies=[Depends(require_auth)])
def list_cameras(request: Request) -> list[dict]:
    cameras = registry.list_cameras()
    statuses = {item["id"]: item for item in _camera_runtime_statuses(request, cameras)}
    out = []
    for cam in cameras:
        d = _camera_out(cam)
        d.update(statuses[cam.camera_id])
        out.append(d)
    return out


@router.get("/cameras/status", dependencies=[Depends(require_auth)])
def camera_statuses(request: Request) -> list[dict]:
    """Small polling surface for online/recording indicators; contains no credentials."""

    return _camera_runtime_statuses(request, registry.list_cameras())


def _probe_and_store(cam: registry.Camera) -> registry.Camera:
    """Detect the driver, probe live capabilities (PTZ, audio/video, ports) and persist them."""
    caps = drivers.probe(cam, active_scan.enumerate_ports(cam.last_ip))
    return registry.upsert_camera(cam.mac, camera_id=cam.camera_id, capabilities=caps.to_dict())


def _camera_from_reference(camera_id: str) -> registry.Camera | None:
    """Resolve the public ID, retaining a bounded MAC fallback for pre-0.1 API clients."""

    return resolve_camera(camera_id)


@router.post("/cameras", dependencies=[Depends(require_auth)])
def upsert_camera(body: CameraIn, request: Request) -> dict:
    # Validate the RTSP credentials before saving: a camera that authenticates now streams later.
    # Reject a wrong password up front instead of storing a camera that can never load (the
    # best-effort capability probe below would otherwise swallow the 401). Only a definitive auth
    # rejection blocks the add — an offline/unreachable camera stays addable and retries later.
    if body.last_ip:
        result = rtsp.check_credentials(
            body.last_ip,
            body.rtsp_port or registry.DEFAULT_RTSP_PORT,
            body.stream_path or "/onvif1",
            body.username or "",
            body.password or "",
        )
        if result == "auth":
            # 422, NOT 401: 401 is reserved for *dashboard session* auth (the frontend redirects to
            # login on any 401). This is a bad *camera* password — a validation error on the body.
            raise HTTPException(
                status_code=422,
                detail="camera rejected these credentials (wrong username or password)",
            )
    cam = registry.upsert_camera(
        body.mac,
        name=body.name,
        username=body.username,
        password=body.password,
        stream_path=body.stream_path,
        rtsp_port=body.rtsp_port,
        last_ip=body.last_ip,
        vendor=body.vendor,
    )
    # Probe capabilities as part of configuring the camera, so device controls (PTZ, audio, ...)
    # light up immediately without a separate manual "probe" step. Best-effort: a slow/failed
    # probe must not fail the add — the camera is already saved and can be re-probed by hand.
    if cam.last_ip:
        try:
            cam = _probe_and_store(cam)
        except Exception as exc:
            log.warning("capability probe on add failed for %s: %s", cam.mac, exc)
    _resync(request)
    return _camera_out(cam)


@router.delete("/cameras/{camera_id}", dependencies=[Depends(require_auth)])
def delete_camera(camera_id: str, request: Request) -> dict:
    camera = _camera_from_reference(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    registry.delete_camera_by_id(camera.camera_id)
    _resync(request)
    return {"ok": True}


# --- device control / capability probe (routed through the camera's driver) --------


@router.post("/cameras/{camera_id}/probe", dependencies=[Depends(require_auth)])
def probe_camera(camera_id: str) -> dict:
    """Detect the camera's driver and probe its live capabilities (PTZ, audio/video, ports)."""
    cam = _camera_from_reference(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    if not cam.last_ip:
        raise HTTPException(status_code=409, detail="camera has no known IP; run a scan first")
    return _camera_out(_probe_and_store(cam))


@router.post("/cameras/{camera_id}/ptz", dependencies=[Depends(require_auth)])
def ptz_move(camera_id: str, body: PtzIn) -> dict:
    """Pan/tilt the camera. ``action``: ``start``/``stop`` for press-and-hold, ``step`` for a nudge."""
    cam = _camera_from_reference(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        ok = drivers.for_camera(cam).ptz(cam, body.direction, (body.action or "step").lower())
    except drivers.Unsupported as exc:
        raise HTTPException(status_code=501, detail="this camera doesn't support PTZ") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=502, detail="camera did not accept the PTZ command")
    return {
        "ok": True,
        "action": (body.action or "step").lower(),
        "direction": (body.direction or "").lower(),
    }


@router.post("/cameras/{camera_id}/reboot", dependencies=[Depends(require_auth)])
def reboot_camera(camera_id: str) -> dict:
    """Reboot the camera in software, if its driver supports it (e.g. ONVIF SystemReboot)."""
    cam = _camera_from_reference(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    try:
        ok = drivers.for_camera(cam).reboot(cam)
    except drivers.Unsupported as exc:
        raise HTTPException(
            status_code=501, detail="this camera doesn't support software reboot"
        ) from exc
    if not ok:
        raise HTTPException(status_code=502, detail="camera did not accept the reboot command")
    return {"ok": True, "rebooting": True}


# --- discovery ----------------------------------------------------------------------


@router.post("/discovery/scan", dependencies=[Depends(require_auth)])
def discovery_scan(request: Request, username: str = "", password: str = "") -> dict:
    """Gentle subnet scan. Returns known cameras (IP refreshed) and new candidates."""
    hosts = active_scan.scan(username=username, password=password)

    def on_rekey(old: str, new: str) -> None:
        # Only the deprecated recording-index MAC projection follows a corrected native value.
        # Opaque media/process/archive identities remain stable, so no service restart is needed.
        try:
            recorder.rekey_segments(old, new)
        except Exception as exc:  # never fail a scan over a housekeeping move
            log.warning("could not migrate recordings %s -> %s: %s", old, new, exc)

    configured, candidates = registry.reconcile(hosts, on_rekey=on_rekey)
    # Cameras added before the probe-on-add change carry no capabilities, so their controls (PTZ,
    # audio) stay dark until someone clicks "probe" by hand. A scan is the natural moment to fill
    # that in: the camera just answered, and this is already the slow, user-initiated path. Best-
    # effort per camera — these cheap cams are probed gently and a failure must not fail the scan.
    for i, cam in enumerate(configured):
        if cam.capabilities or not cam.last_ip:
            continue
        try:
            configured[i] = _probe_and_store(cam)
        except Exception as exc:
            log.warning("backfill capability probe failed for %s: %s", cam.mac, exc)
    return {
        "configured": [_camera_out(c) for c in configured],
        "candidates": [
            {
                "mac": c.mac,
                "ip": c.ip,
                "open_ports": c.open_ports,
                "suggested_path": c.suggested_path,
                "suggested_username": c.suggested_username,
                "vendor": c.vendor,
                "model": c.model,
                "firmware": c.firmware,
                "driver": c.driver,
            }
            for c in candidates
        ],
    }


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


@router.get("/provisioning/status", dependencies=_BLE_PROVISIONING, tags=["provisioning"])
def provisioning_status() -> dict:
    """Describe the local onboarding surface without probing or changing any camera."""
    material_path = get_settings().provisioning_ble_material_file
    native_account = account_store.get_account() is not None
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
    tags=["provisioning"],
)
def provisioning_vendor_account_status(response: Response) -> dict:
    """Report enrollment state without disclosing an account identity or token."""

    configured = account_store.get_account() is not None
    response.headers["Cache-Control"] = "no-store"
    return {
        "provider": account_store.PROVIDER,
        "configured": configured,
        "renewable_session": configured,
        "vendor_cloud_required": True,
    }


@router.post(
    "/provisioning/vendor-account/login",
    dependencies=_LOCAL_PROVISIONING,
    tags=["provisioning"],
)
def provisioning_vendor_account_login(
    body: ProvisioningVendorAccountLoginIn,
    response: Response,
) -> dict:
    """Establish and encrypt a renewable native session; Android/Frida are not involved."""

    try:
        credentials = AccountCredentials.from_password(
            account_type=body.account_type,
            account=body.account.strip(),
            password=body.password.get_secret_value(),
            mobile_area=body.mobile_area,
            language=body.language,
            region=body.region,
            area=body.area,
        )
        session = login_account(credentials)
        account_store.save_account(credentials, session)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VendorAccountError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "provider": account_store.PROVIDER,
        "configured": True,
        "renewable_session": True,
    }


@router.post(
    "/provisioning/vendor-account/refresh",
    dependencies=_LOCAL_PROVISIONING,
    tags=["provisioning"],
)
def provisioning_vendor_account_refresh(response: Response) -> dict:
    """Renew the encrypted native session without returning any credential material."""

    stored = account_store.get_account()
    if stored is None:
        raise HTTPException(status_code=409, detail="vendor account is not configured")
    try:
        refreshed = refresh_account_session(stored.session)
        account_store.update_session(refreshed)
    except VendorAccountError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {
        "provider": account_store.PROVIDER,
        "configured": True,
        "renewable_session": True,
        "refreshed": True,
    }


@router.post("/provisioning/inspect", dependencies=_BLE_PROVISIONING, tags=["provisioning"])
def provisioning_inspect(body: ProvisioningLabelIn) -> dict:
    """Validate and decode a scanned/typed factory label without contacting the camera."""
    return _inspect_provisioning_label(body)


@router.get("/provisioning/networks", dependencies=_BLE_PROVISIONING, tags=["provisioning"])
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


@router.post("/provisioning/networks/manual", dependencies=_BLE_PROVISIONING, tags=["provisioning"])
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


@router.post("/provisioning/start", dependencies=_LOCAL_PROVISIONING, tags=["provisioning"])
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


@router.post("/provisioning/ble/prepare", dependencies=_BLE_PROVISIONING, tags=["provisioning"])
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
        stored = account_store.get_account()
        if stored is not None:
            refreshed = refresh_account_session(stored.session)
            account_store.update_session(refreshed)
            material = fetch_native_ble_material(
                refreshed,
                device_id=identity["device_id"],
            )
        elif settings.provisioning_ble_material_file is not None:
            # Temporary research bridge retained for existing installations. A configured native
            # account always wins and removes all runtime dependence on Frida/capture files.
            material = load_ble_provisioning_material(
                settings.provisioning_ble_material_file,
                expected_device_id=identity["device_id"],
                max_age_seconds=settings.provisioning_ble_material_max_age_seconds,
            )
        else:
            raise HTTPException(
                status_code=503,
                detail="BLE handshake material is unavailable; configure the vendor account",
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
    except (VendorAccountError, VendorProvisioningCloudError) as exc:
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


@router.post(
    "/provisioning/ble/decode-response", dependencies=_BLE_PROVISIONING, tags=["provisioning"]
)
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
    tags=["provisioning"],
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
    tags=["provisioning"],
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
    tags=["provisioning"],
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
    tags=["provisioning"],
)
def provisioning_privileged_p2p_probe(body: ProvisioningLabelIn, response: Response) -> dict:
    """Authenticate to the P2P access node and inspect inventory without contacting the camera."""
    identity = _inspect_provisioning_label(body)
    try:
        enrollment = bound_privileged_enrollment(identity["device_id"])
        inventory = probe_account_inventory(enrollment)
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except P2PProbeError as exc:
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
    tags=["provisioning"],
)
def provisioning_privileged_p2p_route_probe(body: ProvisioningLabelIn, response: Response) -> dict:
    """Prove the selected camera's direct P2P route without media or control commands."""
    identity = _inspect_provisioning_label(body)
    try:
        enrollment = bound_privileged_enrollment(identity["device_id"])
        route = probe_camera_route(enrollment)
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except P2PProbeError as exc:
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
    tags=["provisioning"],
)
def provisioning_privileged_p2p_property_read(
    body: ProvisioningP2PPropertyReadIn, response: Response
) -> dict:
    """Read one allowlisted thing-model property from exactly the requested camera."""
    identity = _inspect_provisioning_label(body)
    if body.property_path not in MODEL_READ_PATHS:
        raise HTTPException(status_code=422, detail="thing-model path is not read-only allowlisted")
    try:
        enrollment = bound_privileged_enrollment(identity["device_id"])
        result = read_camera_property(enrollment, body.property_path)
    except PrivilegedEnrollmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except P2PProbeError as exc:
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


@router.get("/storage", dependencies=[Depends(require_auth)])
def storage_status(request: Request) -> dict:
    monitor = getattr(request.app.state, "storage", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="storage monitor not running")
    st = monitor.state()
    return st.__dict__
