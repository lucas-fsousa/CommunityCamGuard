"""Native Yoosee/Gwell account login and session renewal.

This module is a production reimplementation of the contracts recovered from
the Android APK.  It does not import research helpers, read Frida captures or
log credentials.  The vendor cloud is still required; callers must therefore
classify this transport as cloud-native, never LAN-only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOST = "openapi-iot.cloudlinks.cn"
LOGIN_PATH = "/openapi/app/user/login/account"
REFRESH_PATH = "/openapi/app/user/refreshUserToken"

# Public constants embedded in Yoosee 00.46.06.36. They identify the client
# build but are not account credentials.
APP_VERSION = "3016228"
HEADER_APP_ID = "d591b466644a0420e5f29aefb0cf0088"
ANONYMOUS_APP_TAG = HEADER_APP_ID + APP_VERSION
BODY_APP_ID = "adf33ae6eaa1439b48841fc330ffef11"
BODY_APP_TOKEN = (
    "60ded395c2bdad3a2610ac6150550f551"
    "bb911eb6e6f7106c40c3bb1457bb07d"
)
BASE32_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
BASE32_XOR = 0x7E18FC2D035A4B69
UINT64_MASK = (1 << 64) - 1
INTEGER_BODY_NAMES = {"terminalOS", "apiVersion", "platform", "accessId", "funcSupport"}


class VendorAccountError(RuntimeError):
    """Sanitized login/refresh failure safe to expose to trusted local code."""


@dataclass(frozen=True, slots=True)
class AnonymousPair:
    access_id: str
    secret_key: str


@dataclass(frozen=True, slots=True)
class AccountCredentials:
    account_type: str
    account: str
    password_md5: str
    unique_id: str
    mobile_area: str = "0"
    language: str = "en"
    region: str = "US"
    area: str = "us"

    def __post_init__(self) -> None:
        if self.account_type not in {"email", "mobile", "userId"}:
            raise ValueError("account type must be email, mobile or userId")
        if not self.account:
            raise ValueError("account identity must not be empty")
        digest = self.password_md5.upper()
        if len(digest) != 32 or any(ch not in "0123456789ABCDEF" for ch in digest):
            raise ValueError("password digest must contain 32 hexadecimal characters")
        if not self.unique_id:
            raise ValueError("account unique ID must not be empty")
        if not all(
            isinstance(value, str)
            for value in (self.mobile_area, self.language, self.region, self.area)
        ):
            raise ValueError("account locale fields must be strings")
        object.__setattr__(self, "password_md5", digest)

    @classmethod
    def from_password(
        cls,
        *,
        account_type: str,
        account: str,
        password: str,
        unique_id: str | None = None,
        mobile_area: str = "0",
        language: str = "en",
        region: str = "US",
        area: str = "us",
    ) -> AccountCredentials:
        if not password:
            raise ValueError("password must not be empty")
        return cls(
            account_type=account_type,
            account=account,
            password_md5=yoosee_password_md5(password),
            unique_id=unique_id or str(uuid.uuid4()),
            mobile_area=mobile_area,
            language=language,
            region=region,
            area=area,
        )


@dataclass(frozen=True, slots=True)
class AccountSession:
    access_id: str
    access_token: bytes
    common: Mapping[str, str]
    headers: Mapping[str, str]
    expire_time: str | int | None
    terminal_id: str
    user_id: str

    def __post_init__(self) -> None:
        if not self.access_id or not self.access_id.lstrip("-").isdigit():
            raise ValueError("account access ID must be numeric")
        if len(self.access_token) != 64:
            raise ValueError("account access token must contain 64 bytes")
        if not self.terminal_id or not self.terminal_id.lstrip("-").isdigit():
            raise ValueError("account terminal ID must be numeric")
        if not self.user_id or not self.user_id.lstrip("-").isdigit():
            raise ValueError("account user ID must be numeric")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.common.items()):
            raise ValueError("account common fields must be strings")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.headers.items()):
            raise ValueError("account headers must be strings")
        if self.headers.get("x-iotvideo-accessid") != self.access_id:
            raise ValueError("account header identity does not match the session")
        object.__setattr__(self, "access_token", bytes(self.access_token))
        object.__setattr__(self, "common", dict(self.common))
        object.__setattr__(self, "headers", dict(self.headers))

    @property
    def p2p_access_id(self) -> int:
        return int(self.access_id) & UINT64_MASK

    @property
    def server_user_id(self) -> int:
        value = int(self.user_id)
        if not -(1 << 31) <= value < (1 << 31):
            raise ValueError("account user ID does not fit the APK's Java int")
        return (value & 0x7FFFFFFF) | 0x80000000


PostFunction = Callable[[str, bytes, Mapping[str, str], float], tuple[int, bytes]]


def yoosee_password_md5(password: str) -> str:
    """Mirror the APK's uppercase MD5 helper and its UTF-8 length quirk."""

    encoded = password.encode("utf-8")
    java_utf16_units = len(password.encode("utf-16-le")) // 2
    return hashlib.md5(encoded[:java_utf16_units]).hexdigest().upper()


def _bkdr_hash(value: str) -> int:
    result = 0
    for byte in value.encode():
        result = ((result * 131) + byte) & UINT64_MASK
    return result


def _base32_id(value: int) -> str:
    encoded = (value & UINT64_MASK) ^ BASE32_XOR
    output: list[str] = []
    for shift in range(60, -1, -5):
        digit = (encoded >> shift) & 0x1F
        if output or digit:
            output.append(BASE32_ALPHABET[digit])
    return "".join(output) or "0"


def generate_anonymous_pair(
    *, timestamp: int | None = None, random_low5: int | None = None
) -> AnonymousPair:
    now = int(time.time()) if timestamp is None else int(timestamp)
    rand5 = secrets.randbelow(32) if random_low5 is None else int(random_low5)
    if not 0 <= rand5 <= 31:
        raise ValueError("anonymous random value must be in range 0..31")
    raw = (
        (7 << 61)
        | ((_bkdr_hash(ANONYMOUS_APP_TAG) & 0xFFFFFF) << 32)
        | (((now // 60) & 0x7FFFFFF) << 5)
        | rand5
    )
    octets = bytearray(raw.to_bytes(8, "little"))
    middle = bytearray(octets[1:7])
    for index in range(6):
        middle[index] ^= middle[(index + 1) % 6]
    octets[1:7] = middle
    access_id = str(int.from_bytes(octets, "little"))
    secret = hmac.new(
        ANONYMOUS_APP_TAG.encode(), _base32_id(int(access_id)).encode(), hashlib.md5
    ).hexdigest()
    return AnonymousPair(access_id, secret)


def _signature(fields: Mapping[str, str], key: bytes) -> str:
    content = "\n".join(f"{name}:{fields[name]}" for name in sorted(fields))
    return base64.b64encode(hmac.new(key, content.encode(), hashlib.sha1).digest()).decode()


def build_login_body(credentials: AccountCredentials) -> bytes:
    identity_key = {"email": "email", "mobile": "mobile", "userId": "userId"}[
        credentials.account_type
    ]
    data: dict[str, object] = {
        identity_key: credentials.account,
        "loginMode": credentials.account_type,
        "pwd": credentials.password_md5,
        "uniqueId": credentials.unique_id,
    }
    if credentials.account_type == "mobile":
        data["mobileArea"] = credentials.mobile_area
    data.update(
        {
            "language": credentials.language,
            "terminalOS": 3,
            "accessToken": "",
            "pkgName": "com.yoosee",
            "appVersion": APP_VERSION,
            "appName": "Yoosee",
            "appId": BODY_APP_ID,
            "appToken": BODY_APP_TOKEN,
            "apiVersion": 2,
            "platform": 1,
            "channel": "china",
            "region": credentials.region,
            "accessId": -1,
            "funcSupport": 1,
        }
    )
    return json.dumps(data, separators=(",", ":")).encode()


def build_login_request(
    credentials: AccountCredentials,
    *,
    timestamp: int | None = None,
    nonce: int | None = None,
    random_low5: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    now = int(time.time()) if timestamp is None else int(timestamp)
    nonce_value = secrets.randbelow(2_147_483_648) if nonce is None else int(nonce)
    if not 0 <= nonce_value <= 2_147_483_647:
        raise ValueError("login nonce must be in range 0..2147483647")
    pair = generate_anonymous_pair(timestamp=now, random_low5=random_low5)
    body = build_login_body(credentials)
    fields = {
        "host": HOST,
        "payload": hashlib.sha256(body).hexdigest(),
        "x-iotvideo-accessid": pair.access_id,
        "x-iotvideo-appid": HEADER_APP_ID,
        "x-iotvideo-appver": APP_VERSION,
        "x-iotvideo-nonce": str(nonce_value),
        "x-iotvideo-timestamp": str(now),
    }
    headers: dict[str, str] = {
        "x-iotvideo-anonymous": "anonymous",
        "x-iotvideo-accessid": pair.access_id,
        "x-iotvideo-nonce": str(nonce_value),
        "x-iotvideo-timestamp": str(now),
        "x-iotvideo-area": credentials.area,
        "x-iotvideo-appver": APP_VERSION,
        "x-iotvideo-appid": HEADER_APP_ID,
        "x-iotvideo-uniqueid": credentials.unique_id,
        "x-iotvideo-signature": _signature(fields, pair.secret_key.encode()),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return body, headers


def _api_payload(payload: bytes, *, operation: str) -> tuple[dict[str, object], dict[str, object]]:
    try:
        root = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VendorAccountError(f"vendor {operation} returned an invalid response") from exc
    if not isinstance(root, dict):
        raise VendorAccountError(f"vendor {operation} returned an invalid response")
    code = root.get("code")
    if isinstance(code, str) and code.lstrip("-").isdigit():
        code = int(code)
    if code != 0:
        raise VendorAccountError(f"vendor {operation} rejected the request (code={code})")
    data = root.get("data")
    if not isinstance(data, dict):
        raise VendorAccountError(f"vendor {operation} returned no session data")
    return root, data


def parse_login_response(
    payload: bytes,
    credentials: AccountCredentials,
    request_headers: Mapping[str, str],
) -> AccountSession:
    _root, data = _api_payload(payload, operation="account login")
    access_id = data.get("accessId")
    token_hex = data.get("accessToken")
    terminal_id = data.get("terminalId")
    user_id = data.get("userId")
    if not isinstance(access_id, (str, int)) or not isinstance(token_hex, str):
        raise VendorAccountError("vendor account login returned incomplete credentials")
    if not isinstance(terminal_id, (str, int)) or not isinstance(user_id, (str, int)):
        raise VendorAccountError("vendor account login returned no provisioning identity")
    try:
        token = bytes.fromhex(token_hex)
    except ValueError as exc:
        raise VendorAccountError("vendor account login returned an invalid token") from exc
    if len(token) != 64:
        raise VendorAccountError("vendor account login returned an invalid token")
    access_text = str(access_id)
    area_value = data.get("area")
    area = area_value if isinstance(area_value, str) else credentials.area
    common = {
        "language": credentials.language,
        "terminalOS": "3",
        "accessToken": token[:48].hex(),
        "pkgName": "com.yoosee",
        "appVersion": APP_VERSION,
        "appName": "Yoosee",
        "appId": BODY_APP_ID,
        "appToken": BODY_APP_TOKEN,
        "apiVersion": "2",
        "platform": "1",
        "channel": "china",
        "region": credentials.region,
        "regRegion": "",
        "accessId": access_text,
        "funcSupport": "1",
    }
    headers: dict[str, str] = {
        "x-iotvideo-accessid": access_text,
        "x-iotvideo-area": area,
        "x-iotvideo-appver": APP_VERSION,
        "x-iotvideo-appid": HEADER_APP_ID,
        "x-iotvideo-uniqueid": request_headers["x-iotvideo-uniqueid"],
    }
    expire_time = data.get("expireTime")
    if not isinstance(expire_time, (str, int)):
        expire_time = None
    return AccountSession(
        access_id=access_text,
        access_token=token,
        common=common,
        headers=headers,
        expire_time=expire_time,
        terminal_id=str(terminal_id),
        user_id=str(user_id),
    )


def build_refresh_request(
    session: AccountSession,
    *,
    timestamp: int | None = None,
    nonce: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    data: dict[str, object] = dict(session.common)
    data["accessToken"] = session.access_token[:48].hex()
    for name in INTEGER_BODY_NAMES:
        if name in data:
            data[name] = int(str(data[name]))
    body = json.dumps(data, separators=(",", ":")).encode()
    headers = build_authenticated_headers(
        session,
        body,
        timestamp=timestamp,
        nonce=nonce,
    )
    return body, headers


def build_authenticated_headers(
    session: AccountSession,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: int | None = None,
) -> dict[str, str]:
    """Sign one authenticated vendor-cloud body with the session's private suffix."""

    now = int(time.time()) if timestamp is None else int(timestamp)
    nonce_value = secrets.randbelow(2_147_483_647) + 1 if nonce is None else int(nonce)
    if not 1 <= nonce_value <= 2_147_483_647:
        raise ValueError("authenticated nonce must be in range 1..2147483647")
    fields = {
        "host": HOST,
        "payload": hashlib.sha256(body).hexdigest(),
        "x-iotvideo-accessid": session.access_id,
        "x-iotvideo-nonce": str(nonce_value),
        "x-iotvideo-timestamp": str(now),
    }
    headers = {
        **session.headers,
        "x-iotvideo-nonce": str(nonce_value),
        "x-iotvideo-timestamp": str(now),
        "x-iotvideo-signature": _signature(fields, session.access_token[48:64]),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return headers


def parse_refresh_response(payload: bytes, session: AccountSession) -> AccountSession:
    _root, data = _api_payload(payload, operation="session refresh")
    token_hex = data.get("accessToken")
    if not isinstance(token_hex, str):
        raise VendorAccountError("vendor session refresh returned no token")
    try:
        prefix = bytes.fromhex(token_hex)
    except ValueError as exc:
        raise VendorAccountError("vendor session refresh returned an invalid token") from exc
    if len(prefix) == 48:
        token = prefix + session.access_token[48:64]
    elif len(prefix) == 64:
        token = prefix
    else:
        raise VendorAccountError("vendor session refresh returned an invalid token")
    common = dict(session.common)
    common["accessToken"] = token[:48].hex()
    expire_time = data.get("expireTime", session.expire_time)
    if not isinstance(expire_time, (str, int)):
        expire_time = session.expire_time
    return AccountSession(
        access_id=session.access_id,
        access_token=token,
        common=common,
        headers=session.headers,
        expire_time=expire_time,
        terminal_id=session.terminal_id,
        user_id=session.user_id,
    )


def _post(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except (OSError, URLError) as exc:
        raise VendorAccountError("vendor account service is unavailable") from exc


def login_account(
    credentials: AccountCredentials,
    *,
    timeout: float = 15.0,
    post: PostFunction | None = None,
) -> AccountSession:
    body, headers = build_login_request(credentials)
    status, payload = (post or _post)(f"https://{HOST}{LOGIN_PATH}", body, headers, timeout)
    if status != 200:
        raise VendorAccountError(f"vendor account login failed (http={status})")
    return parse_login_response(payload, credentials, headers)


def refresh_account_session(
    session: AccountSession,
    *,
    timeout: float = 15.0,
    post: PostFunction | None = None,
) -> AccountSession:
    body, headers = build_refresh_request(session)
    status, payload = (post or _post)(f"https://{HOST}{REFRESH_PATH}", body, headers, timeout)
    if status != 200:
        raise VendorAccountError(f"vendor session refresh failed (http={status})")
    return parse_refresh_response(payload, session)


def post_authenticated_json(
    session: AccountSession,
    *,
    path: str,
    body: bytes,
    operation: str,
    timeout: float = 15.0,
    post: PostFunction | None = None,
) -> dict[str, object]:
    """Send one signed account request and return only its validated data object."""

    if not path.startswith("/openapi/"):
        raise ValueError("authenticated path must be an OpenAPI path")
    headers = build_authenticated_headers(session, body)
    status, payload = (post or _post)(f"https://{HOST}{path}", body, headers, timeout)
    if status != 200:
        raise VendorAccountError(f"vendor {operation} failed (http={status})")
    _root, data = _api_payload(payload, operation=operation)
    return data
