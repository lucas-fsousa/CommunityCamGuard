"""Pure StreamPipe framing shared by legacy and IoTVideo talk channels."""

from __future__ import annotations

import struct
import time
from collections.abc import Sequence
from dataclasses import dataclass

from .crypto import RC5

V1_MAGIC = bytes.fromhex("ffffff88")
MICROPHONE_STATE_CHANGE = 0x32


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


@dataclass(frozen=True, slots=True)
class BuiltinCommand:
    command: int
    flags: int
    timestamp: int
    payload: bytes


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


def build_builtin_command(
    command: int,
    payload: bytes = b"",
    *,
    flags: int = 0,
    timestamp_us: int | None = None,
) -> bytes:
    """Mirror ``Connection::Impl::send_cmd``'s built-in command body."""

    if not 0 <= command <= 0xFF:
        raise ValueError("command must fit in one byte")
    if not 0 <= flags <= 0x03:
        raise ValueError("only the low two command flag bits are defined")
    if timestamp_us is None:
        timestamp_us = time.time_ns() // 1_000
    return struct.pack("<BBHI", 0, command, flags, timestamp_us & 0xFFFFFFFF) + bytes(payload)


def parse_builtin_command(frame: bytes) -> BuiltinCommand:
    if len(frame) < 8:
        raise ValueError("built-in command body is shorter than eight bytes")
    reserved, command, flags, timestamp = struct.unpack_from("<BBHI", frame)
    if reserved != 0:
        raise ValueError("unexpected built-in command reserved byte")
    if flags & ~0x03:
        raise ValueError("unexpected built-in command flag bits")
    return BuiltinCommand(command, flags, timestamp, frame[8:])


def pack_v1_sequence_user_data(body: bytes) -> bytes:
    """Mirror ``trans_proto_v1::packing_sequence_user_data`` exactly."""

    if len(body) > 0xFFFF:
        raise ValueError("v1 sequenced user-data body is too large")
    return V1_MAGIC + struct.pack("<HH", 0x0300, len(body)) + bytes(20) + bytes(body)


def unpack_v1_sequence_user_data(frame: bytes) -> bytes:
    if len(frame) < 28 or frame[:4] != V1_MAGIC:
        raise ValueError("not a v1 sequenced user-data frame")
    marker, length = struct.unpack_from("<HH", frame, 4)
    if marker != 0x0300 or any(frame[8:28]):
        raise ValueError("invalid v1 sequenced user-data header")
    if len(frame) != length + 28:
        raise ValueError("v1 sequenced user-data length mismatch")
    return frame[28:]


def unpack_v1_user_data_frames(payload: bytes) -> tuple[bytes, ...]:
    """Unpack concatenated compact non-sequenced v1 command records."""

    frames: list[bytes] = []
    cursor = 0
    while cursor < len(payload):
        if cursor + 8 > len(payload) or payload[cursor : cursor + 4] != V1_MAGIC:
            raise ValueError("not a complete v1 user-data record")
        marker, length = struct.unpack_from("<HH", payload, cursor + 4)
        if marker != 0x0200:
            raise ValueError("invalid v1 user-data header")
        end = cursor + 8 + length
        if end > len(payload):
            raise ValueError("truncated v1 user-data body")
        frames.append(payload[cursor + 8 : end])
        cursor = end
    if not frames:
        raise ValueError("empty v1 user-data payload")
    return tuple(frames)


def pack_microphone_command(enabled: bool, *, timestamp_us: int | None = None) -> bytes:
    return pack_v1_sequence_user_data(
        build_builtin_command(
            MICROPHONE_STATE_CHANGE,
            bytes((int(enabled),)),
            timestamp_us=timestamp_us,
        )
    )


def pack_v1_audio_encoding_header(header: V1EncodingHeader) -> bytes:
    """Build the audio-only HEADER_ENC record sent by ``send_av_enc_info(2)``.

    ``LivePlayer`` first serializes a two-byte stream count followed by one
    20-byte audio descriptor. ``trans_proto_v1::packing_header_enc`` then
    copies the first 20 payload bytes behind marker ``0x0500``. This is not the
    camera-originated ``0x01xx`` encoding-header layout parsed below.
    """

    if header.audio_channels not in (1, 2):
        raise ValueError("v1 encoding header supports mono or stereo audio")
    if not 8 <= header.audio_bit_width <= 0xFF or header.audio_bit_width % 8:
        raise ValueError("audio bit width must be an 8-bit multiple fitting u8")
    if not 1 <= header.audio_sample_rate <= 0xFFFFFFFF:
        raise ValueError("intercom sample rate must fit in the native u32 field")
    if not 1 <= header.audio_frame_size <= 0xFFFF:
        raise ValueError("intercom frame size must fit in the native u16 field")

    descriptor = bytearray(20)
    # ARM64 0x105648..0x105688 builds this descriptor directly. It is not the
    # same layout as the camera-originated 0x01xx header parsed below.
    descriptor[1] = 2  # audio stream
    struct.pack_into("<I", descriptor, 4, header.audio_sample_rate)
    struct.pack_into("<H", descriptor, 8, header.audio_frame_size)
    descriptor[10] = header.audio_channels
    descriptor[11] = header.audio_bit_width
    descriptor[12] = header.audio_codec & 0xFF
    descriptor[13] = header.audio_codec_option & 0xFF

    frame = bytearray(28)
    frame[:4] = V1_MAGIC
    struct.pack_into("<H", frame, 4, 0x0500)
    serialized = bytes((0, 1)) + bytes(descriptor)
    frame[8:] = serialized[:20]
    return bytes(frame)


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
