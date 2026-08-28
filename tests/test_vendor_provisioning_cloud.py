from __future__ import annotations

import json

import pytest

from backend.app.drivers.yoosee.p2p.account import AccountSession
from backend.app.provisioning.vendor_cloud import (
    BIND_TOKEN_PATH,
    TANKEY_PATH,
    VendorProvisioningCloudError,
    build_bind_token_body,
    build_tankey_body,
    fetch_native_ble_material,
)


def _session() -> AccountSession:
    access_id = "-12345"
    token = bytes(range(64))
    return AccountSession(
        access_id=access_id,
        access_token=token,
        common={
            "language": "en",
            "terminalOS": "3",
            "accessToken": token[:48].hex(),
            "apiVersion": "2",
            "platform": "1",
            "accessId": access_id,
            "funcSupport": "1",
        },
        headers={
            "x-iotvideo-accessid": access_id,
            "x-iotvideo-area": "br",
            "x-iotvideo-appver": "3016228",
            "x-iotvideo-appid": "test-app-id",
            "x-iotvideo-uniqueid": "test-unique-id",
        },
        expire_time=123,
        terminal_id="-98765",
        user_id="19088743",
    )


def test_native_ble_bodies_match_recovered_apk_types():
    session = _session()
    tankey = json.loads(build_tankey_body(session, "7443576841"))
    bind = json.loads(build_bind_token_body(session))

    assert tankey["deviceId"] == 7443576841
    assert tankey["accessId"] == -12345
    assert tankey["terminalOS"] == 3
    assert bind["accessId"] == "-12345"
    assert bind["termId"] == "-98765"
    assert bind["devId"] is None
    assert bind["expire"] == 0
    assert bind["accessToken"] == session.access_token[:48].hex()


def test_fetch_native_ble_material_uses_only_renewable_account_session():
    observed = []

    def post(url, body, headers, timeout):
        observed.append((url, json.loads(body), headers, timeout))
        if url.endswith(TANKEY_PATH):
            return 200, json.dumps(
                {
                    "code": 0,
                    "data": {
                        "result": {
                            "tanKey": "00112233445566778899aabbccddeeff",
                            "randNumber": "0123456789abcdef0123456789abcdef",
                        }
                    },
                }
            ).encode()
        if url.endswith(BIND_TOKEN_PATH):
            return 200, json.dumps(
                {"code": 0, "data": {"token": "temporary-bind-token"}}
            ).encode()
        raise AssertionError("unexpected URL")

    session = _session()
    material = fetch_native_ble_material(
        session, device_id="7443576841", timeout=9, post=post
    )

    assert len(observed) == 2
    assert all(item[2]["x-iotvideo-signature"] for item in observed)
    assert observed[0][3] == 9
    assert material.device_id == "7443576841"
    assert material.server_user_id == 0x81234567
    assert material.config_token == "temporary-bind-token"
    assert material.cloud_access_token == session.access_token
    assert material.cloud_common == dict(session.common)
    assert material.cloud_headers == dict(session.headers)


def test_fetch_native_ble_material_rejects_incomplete_cloud_response():
    def post(_url, _body, _headers, _timeout):
        return 200, json.dumps({"code": 0, "data": {"tanKey": "short"}}).encode()

    with pytest.raises(VendorProvisioningCloudError, match="incomplete material"):
        fetch_native_ble_material(_session(), device_id="7443576841", post=post)
