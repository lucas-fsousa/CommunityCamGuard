"""Native vendor-cloud acquisition of the short-lived BLE handshake material.

The request contracts were recovered from the Android client, but this production
module depends only on our own account/session implementation.  It is cloud-native,
not LAN-only: the vendor service is still required during this onboarding stage.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping

from ..vendor_p2p.account import (
    INTEGER_BODY_NAMES,
    AccountSession,
    PostFunction,
    post_authenticated_json,
)
from .ble import BleProvisioningMaterial

TANKEY_PATH = "/openapi/netcfg/app/ble/getTanKey"
BIND_TOKEN_PATH = "/openapi/netcfg/cloud/netcfg/genbindtoken"


class VendorProvisioningCloudError(RuntimeError):
    """Sanitized invalid-material response from the provisioning cloud."""


def _common_body(session: AccountSession) -> dict[str, object]:
    data: dict[str, object] = dict(session.common)
    data["accessToken"] = session.access_token[:48].hex()
    return data


def _integer_fields(data: dict[str, object], *, exclude: set[str] | None = None) -> None:
    for name in INTEGER_BODY_NAMES - (exclude or set()):
        value = data.get(name)
        if value is not None:
            data[name] = int(str(value))


def build_tankey_body(session: AccountSession, device_id: str) -> bytes:
    if not device_id.isdigit():
        raise ValueError("camera device ID must be numeric")
    data = _common_body(session)
    data["deviceId"] = int(device_id)
    _integer_fields(data)
    return json.dumps(data, separators=(",", ":")).encode()


def build_bind_token_body(session: AccountSession) -> bytes:
    data = _common_body(session)
    _integer_fields(data, exclude={"accessId"})
    data.update(
        {
            "accessId": session.access_id,
            "devId": None,
            "expire": 0,
            "termId": session.terminal_id,
        }
    )
    return json.dumps(data, separators=(",", ":")).encode()


def _find_string(root: object, name: str) -> str | None:
    if isinstance(root, Mapping):
        value = root.get(name)
        if isinstance(value, str):
            return value
        for nested in root.values():
            found = _find_string(nested, name)
            if found is not None:
                return found
    elif isinstance(root, list):
        for nested in root:
            found = _find_string(nested, name)
            if found is not None:
                return found
    return None


def fetch_native_ble_material(
    session: AccountSession,
    *,
    device_id: str,
    timeout: float = 15.0,
    post: PostFunction | None = None,
) -> BleProvisioningMaterial:
    """Acquire fresh TanKey/random/bind-token material without Android or Frida."""

    tan_data = post_authenticated_json(
        session,
        path=TANKEY_PATH,
        body=build_tankey_body(session, device_id),
        operation="BLE TanKey request",
        timeout=timeout,
        post=post,
    )
    tan_key = _find_string(tan_data, "tanKey")
    random_number = _find_string(tan_data, "randNumber")
    if (
        tan_key is None
        or len(tan_key) != 32
        or any(character not in "0123456789abcdefABCDEF" for character in tan_key)
        or random_number is None
        or len(random_number) != 32
    ):
        raise VendorProvisioningCloudError(
            "vendor BLE TanKey response returned incomplete material"
        )

    bind_data = post_authenticated_json(
        session,
        path=BIND_TOKEN_PATH,
        body=build_bind_token_body(session),
        operation="BLE bind-token request",
        timeout=timeout,
        post=post,
    )
    config_token = _find_string(bind_data, "token")
    if not config_token:
        raise VendorProvisioningCloudError(
            "vendor BLE bind-token response returned incomplete material"
        )

    return BleProvisioningMaterial(
        device_id=device_id,
        tan_key=tan_key,
        random_number=random_number,
        config_token=config_token,
        server_user_id=session.server_user_id,
        captured_at=int(time.time()),
        cloud_access_token=session.access_token,
        cloud_common=dict(session.common),
        cloud_headers=dict(session.headers),
    )
