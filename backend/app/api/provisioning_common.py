"""Shared HTTP contracts and guards for factory provisioning flows."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr

from .. import drivers
from ..auth import require_auth
from ..provisioning import LabelError, inspect_label
from .local_only import require_local_or_remote_ble_request, require_local_request

LOCAL_PROVISIONING = [Depends(require_auth), Depends(require_local_request)]
BLE_PROVISIONING = [Depends(require_auth), Depends(require_local_or_remote_ble_request)]


def onboarding():
    """Resolve onboarding behavior through the registered camera driver."""

    return drivers.onboarding_provider()


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
    """One camera response returned by Web Bluetooth for server-side decoding."""

    attempt_id: str = Field(min_length=20, max_length=128)
    command: int = Field(ge=0, le=255)
    encrypted: bool = False
    data_base64: str = Field(default="", max_length=16384)
    time_area: str = Field(default="UTC", min_length=1, max_length=128)
    time_zone: int = Field(default=0, ge=-50_400, le=50_400)


class ProvisioningPrivilegedBindIn(ProvisioningLabelIn):
    """Explicit second onboarding stage after Wi-Fi has been configured."""

    time_area: str = Field(default="UTC", min_length=1, max_length=128)
    time_zone: int = Field(default=0, ge=-50_400, le=50_400)


class ProvisioningOnlineStatusIn(ProvisioningLabelIn):
    """Read-only config-token lookup pinned to one BLE attempt."""

    attempt_id: str = Field(min_length=20, max_length=128)


class ProvisioningP2PPropertyReadIn(ProvisioningLabelIn):
    """One allowlisted thing-model read for the identified camera."""

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


def inspect_provisioning_label(body: ProvisioningLabelIn) -> dict:
    """Validate a public label model and translate parser failures to HTTP 422."""

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
