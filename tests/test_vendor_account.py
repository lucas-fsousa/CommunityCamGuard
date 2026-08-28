from __future__ import annotations

import hashlib
import json

import pytest

from backend.app.drivers.yoosee.p2p.account import (
    APP_VERSION,
    HEADER_APP_ID,
    AccountCredentials,
    VendorAccountError,
    build_login_request,
    build_refresh_request,
    generate_anonymous_pair,
    login_account,
    parse_login_response,
    parse_refresh_response,
    refresh_account_session,
    yoosee_password_md5,
)


def _credentials() -> AccountCredentials:
    return AccountCredentials.from_password(
        account_type="email",
        account="person@example.invalid",
        password="test-only-password",
        unique_id="00000000-0000-4000-8000-000000000000",
    )


def _login_payload(token: bytes = bytes(range(64))) -> bytes:
    return json.dumps(
        {
            "code": 0,
            "data": {
                "accessId": "-12345",
                "accessToken": token.hex(),
                "expireTime": 123456789,
                "area": "br",
                "terminalId": "-98765",
                "userId": "19088743",
            },
        }
    ).encode()


def test_anonymous_login_request_matches_recovered_vector():
    credentials = _credentials()
    pair = generate_anonymous_pair(timestamp=1_800_000_000, random_low5=7)
    body, headers = build_login_request(
        credentials, timestamp=1_800_000_000, nonce=123456, random_low5=7
    )
    decoded = json.loads(body)

    assert pair.access_id == "16161397428992428039"
    assert pair.secret_key == "306b601ca64734bfc94f069b4f4886de"
    assert decoded["accessId"] == -1
    assert decoded["email"] == credentials.account
    assert decoded["pwd"] == hashlib.md5(b"test-only-password").hexdigest().upper()
    assert headers["x-iotvideo-accessid"] == pair.access_id
    assert headers["x-iotvideo-appid"] == HEADER_APP_ID
    assert headers["x-iotvideo-appver"] == APP_VERSION
    assert len(headers["x-iotvideo-signature"]) == 28
    assert yoosee_password_md5("ábc") == hashlib.md5("ábc".encode()[:3]).hexdigest().upper()


def test_login_response_preserves_all_provisioning_identities():
    credentials = _credentials()
    _body, request_headers = build_login_request(
        credentials, timestamp=1_800_000_000, nonce=1, random_low5=1
    )
    session = parse_login_response(_login_payload(), credentials, request_headers)

    assert session.access_id == "-12345"
    assert session.p2p_access_id == ((-12345) & ((1 << 64) - 1))
    assert session.terminal_id == "-98765"
    assert session.user_id == "19088743"
    assert session.server_user_id == 0x81234567
    assert session.headers["x-iotvideo-area"] == "br"
    assert session.access_token == bytes(range(64))


def test_refresh_replaces_public_prefix_and_preserves_private_suffix_and_identity():
    credentials = _credentials()
    _body, request_headers = build_login_request(
        credentials, timestamp=1_800_000_000, nonce=1, random_low5=1
    )
    session = parse_login_response(_login_payload(), credentials, request_headers)
    body, headers = build_refresh_request(session, timestamp=1_800_000_001, nonce=2)
    replacement = bytes(reversed(range(48)))
    refreshed = parse_refresh_response(
        json.dumps(
            {"code": 0, "data": {"accessToken": replacement.hex(), "expireTime": 999}}
        ).encode(),
        session,
    )

    assert json.loads(body)["accessToken"] == session.access_token[:48].hex()
    assert headers["x-iotvideo-accessid"] == session.access_id
    assert "x-iotvideo-signature" in headers
    assert refreshed.access_token == replacement + session.access_token[48:64]
    assert refreshed.terminal_id == session.terminal_id
    assert refreshed.user_id == session.user_id
    assert refreshed.expire_time == 999


def test_network_entry_points_are_injectable_and_never_return_raw_payloads():
    observed = []

    def post(url, body, headers, timeout):
        observed.append((url, json.loads(body), headers, timeout))
        if url.endswith("/login/account"):
            return 200, _login_payload()
        return 200, json.dumps(
            {"code": 0, "data": {"accessToken": (b"z" * 48).hex(), "expireTime": 1000}}
        ).encode()

    session = login_account(_credentials(), post=post)
    refreshed = refresh_account_session(session, post=post)

    assert refreshed.access_token[:48] == b"z" * 48
    assert len(observed) == 2
    assert observed[0][0].endswith("/openapi/app/user/login/account")
    assert observed[1][0].endswith("/openapi/app/user/refreshUserToken")


def test_vendor_errors_are_sanitized():
    credentials = _credentials()
    _body, request_headers = build_login_request(
        credentials, timestamp=1_800_000_000, nonce=1, random_low5=1
    )
    with pytest.raises(VendorAccountError, match="code=10007") as caught:
        parse_login_response(
            json.dumps({"code": 10007, "msg": "secret backend detail"}).encode(),
            credentials,
            request_headers,
        )
    assert "secret backend detail" not in str(caught.value)
