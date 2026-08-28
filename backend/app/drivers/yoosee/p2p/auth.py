"""Modern IoTVideo GAT connection-auth frame, reconstructed from the vendor SDK.

The implementation mirrors ``giot_eif_generate_conn_authinfo`` and its crypto
helpers in ``libiotvideomulti.so``.  It deliberately accepts already-decoded
credentials and never reads Frida logs or persists secrets.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

IOTVIDEO_AES_IV = b"iotVideo" + b"\x00" * 8
CONN_AUTH_FRAME_LEN = 0x6C
CONN_AUTH_BLOB_LEN = 0x50


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def normalize_aes_key(key: bytes) -> bytes:
    """SDK fallback for AES keys whose length is not 16, 24 or 32 bytes.

    This is the local helper at ``0x19db80``.  The four little-endian words are
    independent byte-at-a-time hashes.  Connection auth supplies a 12-byte key,
    so this normalization path is mandatory there.
    """

    if len(key) in (16, 24, 32):
        return key

    h0 = 0
    h1 = 0x4E67C6A7
    h2 = 0x1505
    h3 = 0
    for byte in key:
        h0 = _u32(h0 * 0x83 + byte)
        h1 = _u32(h1 ^ _u32(byte + _u32(h1 << 5) + (h1 >> 2)))
        h2 = _u32(byte + _u32(h2 << 5) + h2)
        h3 = _u32(byte + _u32(h3 << 6) + _u32(h3 << 16) - h3)
    return struct.pack("<4I", h0, h1, h2, h3)


def aes_cbc(data: bytes, key: bytes, *, encrypt: bool) -> bytes:
    if len(data) % 16:
        raise ValueError("AES-CBC data length must be a multiple of 16")
    cipher = Cipher(algorithms.AES(normalize_aes_key(key)), modes.CBC(IOTVIDEO_AES_IV))
    transform = cipher.encryptor() if encrypt else cipher.decryptor()
    return transform.update(data) + transform.finalize()


def sum_u16(data: bytes | bytearray) -> int:
    """The SDK's ``get_chkval``: sum little-endian halfwords modulo 2**16."""

    if len(data) % 2:
        raise ValueError("checksum input must contain whole 16-bit words")
    return sum(struct.unpack(f"<{len(data) // 2}H", data)) & 0xFFFF


def strdat_hash64(data: bytes) -> bytes:
    """The two-word hash used to derive the optional terminal-auth key."""

    h0 = 0
    h1 = 0x4E67C6A7
    for byte in data:
        h0 = _u32(h0 * 0x83 + byte)
        h1 = _u32(h1 ^ _u32(byte + _u32(h1 << 5) + (h1 >> 2)))
    return struct.pack("<2I", h0, h1)


def build_conn_auth_blob(
    access_token: bytes,
    signed_header: bytes,
    *,
    nonce: bytes | None = None,
) -> bytes:
    """Build the 80-byte auth trailer appended to modern gute frames.

    ``signed_header`` is the frame's eight bytes at offsets ``0x0c..0x13``
    (sequence plus checksum).  Keeping this primitive separate is important:
    certification and several other frame families append the same blob to a
    larger frame instead of sending the standalone ``0x7f/0x00`` envelope.
    """

    if len(access_token) != 64:
        raise ValueError("connection auth requires a 64-byte access token")
    if len(signed_header) != 8:
        raise ValueError("signed header must be exactly eight bytes")
    if nonce is None:
        nonce = secrets.token_bytes(12)
    if len(nonce) != 12:
        raise ValueError("nonce must be exactly 12 bytes")

    blob = bytearray(CONN_AUTH_BLOB_LEN)
    blob[0] = 1
    blob[1] |= 1
    blob[4:16] = nonce
    blob[16:64] = access_token[:48]
    struct.pack_into("<H", blob, 2, sum_u16(blob[4:64]))
    blob[64:80] = hmac.new(
        access_token[48:64], signed_header + bytes(blob[:64]), hashlib.md5
    ).digest()
    blob[16:64] = aes_cbc(bytes(blob[16:64]), bytes(blob[4:16]), encrypt=True)
    return bytes(blob)


def gute_init_checksum(frame: bytes | bytearray) -> int:
    """Checksum used by ``iv_gute_frm_init_chkval`` for this frame family."""

    if len(frame) < 0x18:
        raise ValueError("truncated gute frame")
    declared = struct.unpack_from("<H", frame, 2)[0]
    if declared > len(frame):
        raise ValueError("declared frame length exceeds buffer")

    flags = struct.unpack_from("<I", frame, 0x14)[0]
    encrypted_len = declared - 0x18
    if flags & (1 << 22):
        encrypted_len -= 0x50
    if flags & (1 << 24):
        encrypted_len -= 0x10
    if encrypted_len < 0 or encrypted_len % 4:
        raise ValueError("invalid checksum-covered payload length")

    checksum = flags & 0xFFFFFF
    for offset in (0, 4, 8, 0x0C):
        checksum ^= struct.unpack_from("<I", frame, offset)[0]
    for offset in range(0x18, 0x18 + encrypted_len, 4):
        checksum ^= struct.unpack_from("<I", frame, offset)[0]
    return _u32(checksum)


@dataclass(frozen=True)
class ParsedConnAuth:
    access_id: int
    sequence: int
    terminal_auth: bool
    nonce: bytes
    token_prefix: bytes
    signature_valid: bool
    blob_checksum_valid: bool
    frame_checksum_valid: bool


def build_conn_authinfo(
    access_id: int,
    access_token: bytes,
    *,
    sequence: int = 0,
    terminal_id: int = 0,
    terminal_auth: bool = False,
    nonce: bytes | None = None,
    header_random15: int | None = None,
) -> bytes:
    """Build the SDK's 108-byte ``0x7f/0x00`` connection-auth frame.

    ``access_token`` is the 64-byte binary value obtained by hex-decoding the
    128-character token supplied to ``register()``.  Random values can be
    injected to make byte-level tests deterministic.
    """

    if len(access_token) != 64:
        raise ValueError("connection auth requires a 64-byte access token")
    if nonce is None:
        nonce = secrets.token_bytes(12)
    if len(nonce) != 12:
        raise ValueError("nonce must be exactly 12 bytes")
    if header_random15 is None:
        header_random15 = secrets.randbits(15)
    if not 0 <= header_random15 <= 0x7FFF:
        raise ValueError("header_random15 must fit in 15 bits")

    frame = bytearray(CONN_AUTH_FRAME_LEN + (16 if terminal_auth else 0))
    frame[0] = 0x7F
    struct.pack_into("<H", frame, 2, CONN_AUTH_FRAME_LEN)
    struct.pack_into("<Q", frame, 4, access_id & 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<I", frame, 0x0C, sequence & 0xFFFFFFFF)
    flags = 0x00400000 | (header_random15 << 1)
    struct.pack_into("<I", frame, 0x14, flags)
    if terminal_auth:
        struct.pack_into("<H", frame, 0x18, 1)
    struct.pack_into("<I", frame, 0x10, gute_init_checksum(frame))

    blob = build_conn_auth_blob(access_token, bytes(frame[0x0C:0x14]), nonce=nonce)
    frame[CONN_AUTH_FRAME_LEN - CONN_AUTH_BLOB_LEN : CONN_AUTH_FRAME_LEN] = blob

    if terminal_auth:
        terminal_key = strdat_hash64(f"{access_id}{terminal_id}".encode("ascii"))
        frame[CONN_AUTH_FRAME_LEN:] = hmac.new(
            terminal_key, bytes(blob[64:80]), hashlib.md5
        ).digest()
    return bytes(frame)


def parse_conn_authinfo(frame: bytes, access_token: bytes) -> ParsedConnAuth:
    """Decrypt and verify a connection-auth frame without exposing credentials."""

    if len(access_token) != 64:
        raise ValueError("connection auth requires a 64-byte access token")
    if len(frame) not in (CONN_AUTH_FRAME_LEN, CONN_AUTH_FRAME_LEN + 16):
        raise ValueError("unexpected connection-auth frame size")
    if frame[0] != 0x7F or struct.unpack_from("<H", frame, 2)[0] != CONN_AUTH_FRAME_LEN:
        raise ValueError("not a connection-auth frame")

    terminal_auth = bool(struct.unpack_from("<H", frame, 0x18)[0] & 1)
    blob = bytearray(frame[CONN_AUTH_FRAME_LEN - CONN_AUTH_BLOB_LEN : CONN_AUTH_FRAME_LEN])
    blob[16:64] = aes_cbc(bytes(blob[16:64]), bytes(blob[4:16]), encrypt=False)
    signed = frame[0x0C:0x14] + bytes(blob[:64])
    expected_signature = hmac.new(access_token[48:64], signed, hashlib.md5).digest()
    stored_frame_checksum = struct.unpack_from("<I", frame, 0x10)[0]

    return ParsedConnAuth(
        access_id=struct.unpack_from("<Q", frame, 4)[0],
        sequence=struct.unpack_from("<I", frame, 0x0C)[0],
        terminal_auth=terminal_auth,
        nonce=bytes(blob[4:16]),
        token_prefix=bytes(blob[16:64]),
        signature_valid=hmac.compare_digest(blob[64:80], expected_signature),
        blob_checksum_valid=struct.unpack_from("<H", blob, 2)[0] == sum_u16(blob[4:64]),
        frame_checksum_valid=stored_frame_checksum == gute_init_checksum(frame),
    )


if __name__ == "__main__":
    token = bytes(range(64))
    built = build_conn_authinfo(
        0x1020304050607080,
        token,
        sequence=7,
        nonce=bytes(range(12)),
        header_random15=0x1234,
    )
    parsed = parse_conn_authinfo(built, token)
    assert parsed.token_prefix == token[:48]
    assert parsed.signature_valid and parsed.blob_checksum_valid and parsed.frame_checksum_valid
    print("connection-auth crypto self-test: ok")
