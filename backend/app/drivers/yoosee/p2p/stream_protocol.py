"""Pure StreamPipe framing for the proven legacy Yoosee talk channel."""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from .crypto import RC5

V1_MAGIC = bytes.fromhex("ffffff88")


@dataclass(frozen=True, slots=True)
class V1EncodingHeader:
    marker: int
    audio_codec: int
    audio_codec_option: int
    audio_channels: int
    audio_bit_width: int
    audio_sample_rate: int
    audio_frame_size: int
    video_codec: int
    video_frame_rate: int
    video_width: int
    video_height: int


def _crypt_tlv(payload: bytes, cookie: bytes, *, inner_type: int, encrypt: bool) -> bytes:
    if len(cookie) != 8:
        raise ValueError("CALLING cookie must be eight bytes")
    if encrypt:
        body = bytearray(4 + len(payload))
        body[:2] = bytes((inner_type, 2))
        struct.pack_into("<H", body, 2, len(body))
        body[4:] = payload
        offset = 4
    else:
        if len(payload) < 4 or payload[0] != inner_type:
            raise ValueError("unexpected inner TLV type")
        if struct.unpack_from("<H", payload, 2)[0] != len(payload):
            raise ValueError("inner TLV length mismatch")
        body = bytearray(payload)
        offset = 4
    cipher = RC5(cookie, rounds=6, w=32)
    transform = cipher.encrypt_block if encrypt else cipher.decrypt_block
    for cursor in range(offset, offset + ((len(body) - offset) // 8) * 8, 8):
        body[cursor : cursor + 8] = transform(bytes(body[cursor : cursor + 8]))
    return bytes(body) if encrypt else bytes(body[4:])


def encrypt_command_tlv(payload: bytes, cookie: bytes) -> bytes:
    return _crypt_tlv(payload, cookie, inner_type=2, encrypt=True)


def decrypt_command_tlv(frame: bytes, cookie: bytes) -> bytes:
    return _crypt_tlv(frame, cookie, inner_type=2, encrypt=False)


def encrypt_media_tlv(payload: bytes, cookie: bytes) -> bytes:
    return _crypt_tlv(payload, cookie, inner_type=4, encrypt=True)


def decrypt_media_tlv(frame: bytes, cookie: bytes) -> bytes:
    return _crypt_tlv(frame, cookie, inner_type=4, encrypt=False)


def pack_legacy_capture_header(frame_rate: int = 15) -> bytes:
    if not 1 <= frame_rate <= 0x3F:
        raise ValueError("legacy capture frame rate must be between 1 and 63")
    frame = bytearray(28)
    frame[:4] = V1_MAGIC
    frame[4:11] = bytes.fromhex("000105210114f0")
    frame[11] = frame_rate << 2
    return bytes(frame)


def pack_legacy_talk_control(enabled: bool) -> bytes:
    return V1_MAGIC + bytes((0, 2, 5, int(enabled))) + bytes(20)


def unpack_v1_encoding_header(frame: bytes) -> V1EncodingHeader:
    if len(frame) != 28 or frame[:4] != V1_MAGIC:
        raise ValueError("not a complete v1 encoding header")
    marker = struct.unpack_from("<H", frame, 4)[0]
    if marker >> 8 != 1 or frame[6:8] != b"\x00\x00":
        raise ValueError("invalid v1 encoding-header marker")
    if frame[10] not in (0, 1):
        raise ValueError("invalid v1 audio channel encoding")
    return V1EncodingHeader(
        marker=marker,
        audio_codec=frame[8],
        audio_codec_option=frame[9],
        audio_channels=frame[10] + 1,
        audio_bit_width=(frame[11] + 1) * 8,
        audio_sample_rate=struct.unpack_from("<I", frame, 12)[0],
        audio_frame_size=struct.unpack_from("<H", frame, 16)[0],
        video_codec=frame[18],
        video_frame_rate=frame[19],
        video_width=struct.unpack_from("<I", frame, 20)[0],
        video_height=struct.unpack_from("<I", frame, 24)[0],
    )


def build_v1_audio_packet(
    frames: Sequence[bytes],
    timestamp: int,
    *,
    record_marker: int = 0,
) -> bytes:
    """Pack one or more encoded talk frames in the negotiated v1 AV record."""

    if not frames:
        raise ValueError("at least one audio frame is required")
    if len(frames) > 0xFFFF:
        raise ValueError("too many audio frames")
    if any(not frame or len(frame) > 0xFFFF for frame in frames):
        raise ValueError("audio frames must contain 1..65535 bytes")
    packet = bytearray(28 + 2 * len(frames) + sum(map(len, frames)))
    packet[:4] = V1_MAGIC
    struct.pack_into("<H", packet, 4, record_marker & 0xFFFF)
    struct.pack_into("<H", packet, 6, len(frames))
    struct.pack_into("<Q", packet, 20, timestamp & 0xFFFFFFFFFFFFFFFF)
    cursor = 28
    for frame in frames:
        struct.pack_into("<H", packet, cursor, len(frame))
        cursor += 2
    for frame in frames:
        packet[cursor : cursor + len(frame)] = frame
        cursor += len(frame)
    return bytes(packet)
