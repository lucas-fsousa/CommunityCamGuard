"""Bounded microphone reply metadata; never an acoustic-success assertion."""

import struct
from dataclasses import dataclass

from .stream_protocol import (
    MICROPHONE_STATE_CHANGE,
    unpack_v1_sequence_user_data,
    unpack_v1_user_data_frames,
)


@dataclass(frozen=True, slots=True)
class MicrophoneReply:
    origin: int
    flags: int
    timestamp: int
    payload_size: int
    result: int | None


def microphone_reply(body: bytes) -> MicrophoneReply | None:
    if not 8 <= len(body) <= 16 or body[1] != MICROPHONE_STATE_CHANGE:
        return None
    origin, _, flags, timestamp = struct.unpack_from('<BBHI', body)
    if origin not in (0, 1) or flags & ~3:
        return None
    return MicrophoneReply(origin, flags, timestamp, len(body)-8,
                           struct.unpack_from('<Q', body, 8)[0] if len(body) == 16 else None)


def wrapped_microphone_replies(body: bytes) -> tuple[MicrophoneReply, ...]:
    """Only known V1 wrappers; unknown commands remain unhandled, not fatal."""
    if len(body) > 1024:
        return ()
    try:
        frames = ((unpack_v1_sequence_user_data(body),) if body[4:6] == b'\x00\x03'
                  else unpack_v1_user_data_frames(body))
    except ValueError:
        return ()
    return tuple(reply for frame in frames if (reply := microphone_reply(frame)) is not None)
