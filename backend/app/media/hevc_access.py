"""Bounded inspection of complete Annex-B HEVC access units, not a decoder."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ACCESS_UNIT = 1024 * 1024
MAX_NALS = 128
_START = re.compile(b"\x00\x00(?:\x00)?\x01")


@dataclass(frozen=True, slots=True)
class Nal:
    kind: int
    data: bytes

    @property
    def first_slice(self) -> bool:
        return self.kind < 32 and bool(self.data[2] & 0x80)


def inspect_access_unit(payload: bytes) -> tuple[Nal, ...]:
    if not payload or len(payload) > MAX_ACCESS_UNIT:
        raise ValueError("HEVC access-unit size out of bounds")
    starts: list[tuple[int, int]] = []
    for match in _START.finditer(payload):
        if len(starts) >= MAX_NALS:
            raise ValueError("too many HEVC NAL units")
        starts.append((match.start(), match.end()))
    if not starts or any(payload[:starts[0][0]]):
        raise ValueError("HEVC Annex-B prefix missing")
    result = []
    for index, (_, start) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(payload)
        body = payload[start:end].rstrip(b"\x00")
        if len(body) < 3 or body[0] & 0x80 or not body[1] & 7:
            raise ValueError("invalid HEVC NAL header")
        if ((body[0] & 1) << 5) | (body[1] >> 3):
            raise ValueError("multilayer HEVC is not supported by this gate")
        result.append(Nal((body[0] >> 1) & 63, body))
    video = [nal for nal in result if nal.kind < 32]
    if video and (not video[0].first_slice or sum(nal.first_slice for nal in video) != 1):
        raise ValueError("expected one complete HEVC picture per access unit")
    return tuple(result)
