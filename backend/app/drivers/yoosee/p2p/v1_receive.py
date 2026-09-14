"""Bounded incremental StreamPipe V1 records, isolated from production streams.

Layout follows the recovered trans_proto_v1 packing_avdata: 28-byte header,
u16 audio sizes, audio payloads, then video payload. Timestamps remain raw u64;
no wall-clock conversion, codec decoding or keyframe inference is performed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import NoReturn

from .stream_protocol import V1_MAGIC, V1EncodingHeader, unpack_v1_encoding_header

MAX_RECORD = 1024 * 1024
MAX_CHUNK = 65535
MAX_AUDIO_FRAMES = 256


@dataclass(frozen=True, slots=True)
class V1Record:
    encoding: V1EncodingHeader | None = None
    audio: tuple[bytes, ...] = ()
    video: bytes = b""
    audio_timestamp: int = 0
    video_timestamp: int = 0


class V1Receiver:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self.closed = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def close(self) -> None:
        self._buffer.clear()
        self.closed = True

    def _fail(self, reason: str) -> NoReturn:
        self.close()
        raise ValueError(reason)

    def feed(self, payload: bytes) -> tuple[V1Record, ...]:
        if self.closed:
            raise ValueError("V1 receiver is closed")
        if len(payload) > MAX_CHUNK or len(self._buffer) + len(payload) > MAX_RECORD + MAX_CHUNK:
            self._fail("V1 input budget exceeded")
        self._buffer.extend(payload)
        records: list[V1Record] = []
        cursor = 0
        while len(self._buffer) - cursor >= 28:
            header = self._buffer[cursor:cursor + 28]
            if header[:4] != V1_MAGIC:
                self._fail("V1 record boundary mismatch")
            marker, count = struct.unpack_from("<HH", header, 4)
            if marker >> 8 == 1:
                try:
                    encoding = unpack_v1_encoding_header(bytes(header))
                except ValueError:
                    self._fail("invalid V1 encoding header")
                records.append(V1Record(encoding=encoding))
                cursor += 28
                continue
            if marker not in (0, 8) or count > MAX_AUDIO_FRAMES:
                self._fail("unsupported V1 record type or audio count")
            video_size = struct.unpack_from("<I", header, 8)[0]
            descriptors = 28 + count * 2
            if descriptors + video_size > MAX_RECORD:
                self._fail("V1 record size exceeds budget")
            if len(self._buffer) - cursor < descriptors:
                break
            sizes = struct.unpack_from(f"<{count}H", self._buffer, cursor + 28)
            if any(size == 0 for size in sizes):
                self._fail("invalid V1 audio frame size")
            total = descriptors + sum(sizes) + video_size
            if total > MAX_RECORD:
                self._fail("V1 record size exceeds budget")
            if len(self._buffer) - cursor < total:
                break
            offset = cursor + descriptors
            audio: list[bytes] = []
            for size in sizes:
                audio.append(bytes(self._buffer[offset:offset + size]))
                offset += size
            records.append(V1Record(
                audio=tuple(audio), video=bytes(self._buffer[offset:offset + video_size]),
                audio_timestamp=struct.unpack_from("<Q", header, 20)[0],
                video_timestamp=struct.unpack_from("<Q", header, 12)[0],
            ))
            cursor += total
        del self._buffer[:cursor]
        return tuple(records)
