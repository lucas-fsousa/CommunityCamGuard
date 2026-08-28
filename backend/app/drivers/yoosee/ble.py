"""Recovered Yoosee/Gwell BLE provisioning codec.

The transport itself runs in the browser through Web Bluetooth.  These helpers mirror the two
native APK libraries so the sensitive Wi-Fi payload can be encrypted and fragmented without
shipping the vendor's ARM-only binaries.
"""

from __future__ import annotations

import json
import secrets
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BLE_SERVICE_UUID = "8922a5c3-1e44-403e-a587-bcf972e398b4"
BLE_READ_UUID = "0000fed4-0000-1000-8000-00805f9b34fb"
BLE_WRITE_UUID = "0000fed5-0000-1000-8000-00805f9b34fb"
BLE_INDICATE_UUID = "0000fed6-0000-1000-8000-00805f9b34fb"
BLE_WRITE_WITHOUT_RESPONSE_UUID = "0000fed7-0000-1000-8000-00805f9b34fb"
BLE_NOTIFY_UUID = "0000fed8-0000-1000-8000-00805f9b34fb"

BLE_COMMANDS = {
    "send_random": 0x70,
    "random_response": 0x71,
    "wifi_list": 0x80,
    "wifi_list_response": 0x81,
    "wifi_config": 0x82,
    "wifi_config_response": 0x83,
    # Asynchronous DevBleConnWiFiRes. The immediate 0x83 only acknowledges 0x82; the APK waits
    # for nativeGetBaseCMD(9), which maps to 0x85, before binding with confirmKey.
    "wifi_connection_result": 0x85,
    "finish": 0x88,
    "link_type": 0x72,
    "link_type_response": 0x73,
}

_VENDOR_AES_IV = b"iotVideo" + bytes(8)


class BleCodecError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BleProvisioningMaterial:
    device_id: str
    tan_key: str
    random_number: str
    config_token: str
    server_user_id: int
    captured_at: int
    cloud_access_token: bytes | None = None
    cloud_common: dict[str, str] | None = None
    cloud_headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class BleProvisioningAttempt:
    """One browser handshake pinned to the exact TanKey used to build its frames."""

    attempt_id: str
    material: BleProvisioningMaterial
    expires_at: float


_ATTEMPT_TTL_SECONDS = 180
_attempt_lock = threading.Lock()
_attempts: dict[str, BleProvisioningAttempt] = {}


def _purge_expired_attempts(now: float) -> None:
    expired = [attempt_id for attempt_id, item in _attempts.items() if item.expires_at <= now]
    for attempt_id in expired:
        _attempts.pop(attempt_id, None)


def begin_ble_provisioning_attempt(
    material: BleProvisioningMaterial,
    *,
    now: float | None = None,
) -> BleProvisioningAttempt:
    """Pin fresh file material in memory for the complete multi-response GATT exchange.

    The material file may be refreshed while a browser is waiting for the post-Wi-Fi result.
    Re-reading that file for every response would then decrypt an active exchange with another
    TanKey.  The opaque attempt ID exposes no key and is short-lived.
    """
    current = time.time() if now is None else float(now)
    item = BleProvisioningAttempt(
        attempt_id=secrets.token_urlsafe(32),
        material=material,
        expires_at=current + _ATTEMPT_TTL_SECONDS,
    )
    with _attempt_lock:
        _purge_expired_attempts(current)
        # A camera can perform only one provisioning exchange at a time. Starting over invalidates
        # an older browser attempt for that same physical device, while leaving other cameras alone.
        for attempt_id, existing in list(_attempts.items()):
            if existing.material.device_id == material.device_id:
                _attempts.pop(attempt_id, None)
        _attempts[item.attempt_id] = item
    return item


def ble_provisioning_attempt(
    attempt_id: str,
    *,
    expected_device_id: str,
    now: float | None = None,
) -> BleProvisioningAttempt:
    """Resolve an opaque active attempt without consulting mutable material on disk."""
    current = time.time() if now is None else float(now)
    with _attempt_lock:
        _purge_expired_attempts(current)
        item = _attempts.get(str(attempt_id))
    if item is None:
        raise BleCodecError("BLE provisioning attempt has expired; start the Bluetooth step again")
    if item.material.device_id != str(expected_device_id):
        raise BleCodecError("BLE provisioning attempt belongs to a different camera")
    return item


def _clear_ble_provisioning_attempts_for_tests() -> None:
    with _attempt_lock:
        _attempts.clear()


def load_ble_provisioning_material(
    path: Path,
    *,
    expected_device_id: str,
    max_age_seconds: int,
    now: int | None = None,
) -> BleProvisioningMaterial:
    """Load short-lived research material without ever returning it through the API."""
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise BleCodecError("BLE provisioning material is unavailable")
    if stat.S_IMODE(target.stat().st_mode) & 0o077:
        raise BleCodecError("BLE provisioning material must be readable only by its owner")
    try:
        raw = json.loads(target.read_text())
        cloud_auth = raw.get("cloudAuth")
        cloud_access_token = None
        cloud_common = None
        cloud_headers = None
        if cloud_auth is not None:
            if not isinstance(cloud_auth, dict):
                raise TypeError("cloudAuth must be an object")
            cloud_access_token = bytes.fromhex(str(cloud_auth["accessToken"]))
            cloud_common = {str(key): str(value) for key, value in cloud_auth["common"].items()}
            cloud_headers = {
                str(key).lower(): str(value) for key, value in cloud_auth["headers"].items()
            }
            if len(cloud_access_token) != 64:
                raise ValueError("cloud access token must contain 64 bytes")
        material = BleProvisioningMaterial(
            device_id=str(raw["device_id"]),
            tan_key=str(raw["tanKey"]),
            random_number=str(raw["randNumber"]),
            config_token=str(raw["configToken"]),
            server_user_id=int(raw["serverUserId"]),
            captured_at=int(raw["captured_at"]),
            cloud_access_token=cloud_access_token,
            cloud_common=cloud_common,
            cloud_headers=cloud_headers,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BleCodecError("BLE provisioning material is invalid") from exc
    if material.device_id != str(expected_device_id):
        raise BleCodecError("BLE provisioning material belongs to a different camera")
    age = (int(time.time()) if now is None else int(now)) - material.captured_at
    if age < 0 or age > max_age_seconds:
        raise BleCodecError("BLE provisioning material has expired")
    if not material.config_token or not material.random_number:
        raise BleCodecError("BLE provisioning material is incomplete")
    encrypt_ble_payload(b"", material.tan_key)
    return material


def encrypt_ble_payload(data: bytes, tan_key_hex: str) -> bytes:
    """Mirror ``giot_eif_ble_aes_encrypt``: CBC full blocks, untouched trailing bytes.

    The native SDK deliberately does not pad.  It encrypts ``floor(len(data) / 16) * 16`` bytes
    in-place and leaves any partial final block as plaintext.
    """
    try:
        key = bytes.fromhex(tan_key_hex)
    except ValueError as exc:
        raise BleCodecError("TanKey must be hexadecimal") from exc
    if len(key) not in {16, 24, 32}:
        raise BleCodecError("TanKey must decode to a 16, 24 or 32 byte AES key")

    encrypted_length = len(data) // 16 * 16
    if not encrypted_length:
        return data
    encryptor = Cipher(algorithms.AES(key), modes.CBC(_VENDOR_AES_IV)).encryptor()
    prefix = encryptor.update(data[:encrypted_length]) + encryptor.finalize()
    return prefix + data[encrypted_length:]


def decrypt_ble_payload(data: bytes, tan_key_hex: str) -> bytes:
    """Reverse the vendor's CBC/full-block encryption without touching its trailing bytes."""
    try:
        key = bytes.fromhex(tan_key_hex)
    except ValueError as exc:
        raise BleCodecError("TanKey must be hexadecimal") from exc
    if len(key) not in {16, 24, 32}:
        raise BleCodecError("TanKey must decode to a 16, 24 or 32 byte AES key")

    encrypted_length = len(data) // 16 * 16
    if not encrypted_length:
        return data
    decryptor = Cipher(algorithms.AES(key), modes.CBC(_VENDOR_AES_IV)).decryptor()
    prefix = decryptor.update(data[:encrypted_length]) + decryptor.finalize()
    return prefix + data[encrypted_length:]


@dataclass(frozen=True, slots=True)
class BleFrame:
    version: int
    encrypted: bool
    message_id: int
    command: int
    total_frames: int
    frame_index: int
    data: bytes


@dataclass(frozen=True, slots=True)
class BleMessage:
    version: int
    encrypted: bool
    message_id: int
    command: int
    data: bytes


def parse_ble_frame(raw: bytes) -> BleFrame:
    """Parse and validate the four-byte ``gis_split_packet`` frame header."""
    frame = bytes(raw)
    if len(frame) < 4:
        raise BleCodecError("BLE frame is shorter than its four-byte header")

    control, command, sequence, data_length = frame[:4]
    total_frames = sequence >> 4
    frame_index = sequence & 0x0F
    message_id = control & 0x0F
    if message_id == 0:
        raise BleCodecError("BLE message ID zero is reserved")
    if total_frames == 0:
        raise BleCodecError("BLE frame count must be between 1 and 15")
    if frame_index >= total_frames:
        raise BleCodecError("BLE frame index is outside the declared message")
    if len(frame) != 4 + data_length:
        raise BleCodecError("BLE frame data length does not match its header")

    return BleFrame(
        version=(control >> 5) & 0x07,
        encrypted=bool(control & 0x10),
        message_id=message_id,
        command=command,
        total_frames=total_frames,
        frame_index=frame_index,
        data=frame[4:],
    )


@dataclass(slots=True)
class BleMessageAssembler:
    """Reassemble one fragmented notification, including out-of-order GATT deliveries."""

    _key: tuple[int, bool, int, int, int] | None = None
    _parts: dict[int, bytes] = field(default_factory=dict)

    def reset(self) -> None:
        self._key = None
        self._parts.clear()

    def add(self, raw: bytes) -> BleMessage | None:
        frame = parse_ble_frame(raw)
        key = (
            frame.version,
            frame.encrypted,
            frame.message_id,
            frame.command,
            frame.total_frames,
        )
        if key != self._key:
            self._key = key
            self._parts.clear()

        existing = self._parts.get(frame.frame_index)
        if existing is not None and existing != frame.data:
            raise BleCodecError("Conflicting duplicate BLE frame")
        self._parts[frame.frame_index] = frame.data
        if len(self._parts) != frame.total_frames:
            return None

        message = BleMessage(
            version=frame.version,
            encrypted=frame.encrypted,
            message_id=frame.message_id,
            command=frame.command,
            data=b"".join(self._parts[index] for index in range(frame.total_frames)),
        )
        self.reset()
        return message


def fragment_ble_message(
    *,
    command: int,
    data: bytes,
    encrypted: bool,
    message_id: int,
    mtu: int,
    version: int = 0,
) -> list[bytes]:
    """Mirror ``gis_split_packet`` and return the GATT writes in wire order."""
    if not 0 <= command <= 0xFF:
        raise BleCodecError("BLE command must fit in one byte")
    if not 1 <= message_id <= 0x0F:
        raise BleCodecError("BLE message ID must be between 1 and 15")
    if not 0 <= version <= 0x07:
        raise BleCodecError("BLE frame version must be between 0 and 7")
    if not 5 <= mtu <= 0x100:
        raise BleCodecError("BLE MTU must be between 5 and 256")

    capacity = mtu - 4
    count = max(1, (len(data) + capacity - 1) // capacity)
    if count > 0x0F:
        raise BleCodecError("BLE payload requires more than 15 frames")

    control = (version << 5) | (int(encrypted) << 4) | message_id
    frames: list[bytes] = []
    for index in range(count):
        chunk = data[index * capacity : (index + 1) * capacity]
        frames.append(bytes((control, command, (count << 4) | index, len(chunk))) + chunk)
    return frames


def build_ble_provisioning_frames(
    material: BleProvisioningMaterial,
    *,
    wifi_payload: str,
    mtu: int = 23,
) -> dict[str, list[bytes]]:
    """Build the exact four-stage Wi-Fi-camera sequence recovered from the APK."""
    payload = wifi_payload.encode("utf-8")
    link_type_payload = json.dumps(
        {"linkType": 1, "linkTypeName": "WIFI"}, separators=(",", ":")
    ).encode("utf-8")
    return {
        "challenge": fragment_ble_message(
            command=BLE_COMMANDS["send_random"],
            data=material.random_number.encode("utf-8"),
            encrypted=False,
            message_id=1,
            mtu=mtu,
        ),
        "link_type": fragment_ble_message(
            command=BLE_COMMANDS["link_type"],
            data=encrypt_ble_payload(link_type_payload, material.tan_key),
            encrypted=True,
            message_id=2,
            mtu=mtu,
        ),
        "wifi_list": fragment_ble_message(
            command=BLE_COMMANDS["wifi_list"],
            data=encrypt_ble_payload(b"\x01", material.tan_key),
            encrypted=True,
            message_id=3,
            mtu=mtu,
        ),
        "wifi_config": fragment_ble_message(
            command=BLE_COMMANDS["wifi_config"],
            data=encrypt_ble_payload(payload, material.tan_key),
            encrypted=True,
            message_id=4,
            mtu=mtu,
        ),
        "finish": fragment_ble_message(
            command=BLE_COMMANDS["finish"],
            data=b"",
            encrypted=False,
            message_id=5,
            mtu=mtu,
        ),
    }
