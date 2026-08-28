"""Factory-new camera provisioning HTTP workflow."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import drivers
from ..config import get_settings
from .provisioning_common import (
    BLE_PROVISIONING,
    onboarding,
)

router = APIRouter(prefix="/api", tags=["provisioning"])
_BLE_PROVISIONING = BLE_PROVISIONING
_onboarding = onboarding


@router.get("/provisioning/status", dependencies=_BLE_PROVISIONING)
def provisioning_status(driver: str | None = None) -> dict:
    """Describe the local onboarding surface without probing or changing any camera."""
    material_path = get_settings().provisioning_ble_material_file
    available = drivers.onboarding_providers()
    if not available:
        raise HTTPException(status_code=503, detail="no factory-onboarding driver is registered")
    # Status is discovery, not an onboarding operation. It may present the first provider so the
    # UI can render a selector; identity and mutation routes still reject ambiguous omission.
    provider = _onboarding(driver) if driver else available[0][1]
    native_account = provider.account_configured()
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
        "driver": provider.driver_key,
        "driver_required": len(available) > 1,
        "providers": [
            {"driver": key, "provider": candidate.provider} for key, candidate in available
        ],
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
