"""Offline SDK 6.45 E4 context extraction; no relay connection or certification."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .platform_metadata import parse_push_stream_platform_metadata


@dataclass(frozen=True, slots=True)
class PushContext:
    """Wire fields only, not proof of certification or permission to send hangup.

    The short session ID is generated locally by the SDK, not carried by E4.
    repr is deliberately credential-free; callers must not serialize this object
    into logs (dataclass serialization does not honor repr=False).
    """

    push_id: int = field(repr=False)
    session_type: int
    token: bytes = field(repr=False)


def parse_push_context(
    frame: bytes, *, expected_device_id: int, expected_link_id: int,
) -> PushContext | None:
    """Decode an already authenticated/decrypted E4 for one outstanding MTP link.

    Matching IDs is correlation, not authentication. Reuses the bounded E4
    envelope/relay-table checks; it neither consumes nor connects to relay URLs.
    Empty/one-byte tokens are unusable for our separately validated hangup codec.
    """
    if parse_push_stream_platform_metadata(
        frame, expected_device_id=expected_device_id, expected_link_id=expected_link_id,
    ) is None:
        return None
    token_size = struct.unpack_from("<H", frame, 0x1E)[0]
    if token_size < 2:
        return None
    return PushContext(
        push_id=struct.unpack_from("<I", frame, 0x2C)[0],
        session_type=frame[0x1D],
        token=bytes(frame[0x88:0x88 + token_size]),
    )
