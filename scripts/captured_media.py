"""Offline session correlation and content-free inspection of complete media TLVs.

Checksums/cookies correlate a historical route, not authenticate a live camera.
Secrets and decoded audio/video are never returned, logged or written to disk.
"""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass, field

from backend.app.drivers.yoosee.p2p.crypto import gute_mode1_decrypt, gute_mode1_xor_checksum
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    decrypt_media_tlv,
    unpack_v1_encoding_header,
)

from .captured_frames import CapturedFrames

Endpoint = tuple[str, int]


@dataclass(frozen=True)
class CallingKey:
    call_id: int = field(repr=False)
    cookie: bytes = field(repr=False)


class CapturedSessions:
    def __init__(self) -> None:
        self._keys: dict[tuple, CallingKey | None] = {}

    def observe(self, source: Endpoint, destination: Endpoint, wire: bytes) -> None:
        if len(wire) != 177 or wire[:2] != b"\x7e\xa4":
            return
        try:
            plain = gute_mode1_decrypt(wire)
        except ValueError:
            return
        flags = struct.unpack_from("<I", plain, 0x14)[0]
        if (struct.unpack_from("<H", plain, 2)[0] != 177
                or (flags >> 16) & 3 != 1
                or struct.unpack_from("<I", plain, 0x10)[0] != gute_mode1_xor_checksum(plain)
                or struct.unpack_from("<H", plain, 0x18)[0] != 0x4483):
            return
        link = struct.unpack_from("<I", plain, 0x1C)[0]
        if not 0 < link <= 0xFFFFFF:
            return
        key = self._index(source, destination, link)
        value = CallingKey(struct.unpack_from("<I", plain, 0x84)[0], plain[0x78:0x80])
        if key not in self._keys:
            if len(self._keys) >= 16:
                raise ValueError("capture exceeds 16 direct session bindings")
            self._keys[key] = value
        elif self._keys[key] != value:
            self._keys[key] = None  # ambiguous reuse: never guess a key

    @staticmethod
    def _index(source: Endpoint, destination: Endpoint, link: int) -> tuple:
        return (*sorted((source, destination)), link)

    def lookup(self, source: Endpoint, destination: Endpoint, conv: int) -> CallingKey | None:
        return self._keys.get(self._index(source, destination, conv & 0x7FFFFFFF))


class CapturedMedia:
    def __init__(self) -> None:
        self._binding: CallingKey | None = None
        self.frames = CapturedFrames()
        self._prefix = bytearray()
        self._invalid = False
        self.report: dict = dict(call_correlated=False, decoded_messages=0, decoded_bytes=0,
                                 skipped_media=0, encoding_header=None)

    def consume(self, message: bytes, key: CallingKey | None) -> None:
        if len(message) < 4 or struct.unpack_from("<H", message, 2)[0] != len(message):
            return
        if message[0] == 3 and len(message) == 76:
            if key is not None and struct.unpack_from("<I", message, 4)[0] == key.call_id:
                if not self._invalid:
                    self._binding = key
                    self.report["call_correlated"] = True
            else:
                self._invalid = True
        if message[0] != 4:
            return
        if (self._invalid or key is None or self._binding != key
                or message[1] not in (1, 2)):
            self.report["skipped_media"] += 1
            return
        payload = decrypt_media_tlv(message, key.cookie)
        self.frames.consume(payload)
        self.report["decoded_messages"] += 1
        self.report["decoded_bytes"] += len(payload)
        if len(self._prefix) < 28:
            self._prefix.extend(payload[:28 - len(self._prefix)])
            if len(self._prefix) == 28:
                try:
                    self.report["encoding_header"] = asdict(unpack_v1_encoding_header(bytes(self._prefix)))
                except ValueError:
                    pass  # never scan arbitrary plaintext for a plausible header
