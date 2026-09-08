"""Socket-free decoding for IoTVideo onboard-playback lifecycle events."""

from __future__ import annotations

import struct
from dataclasses import dataclass

PLAYBACK_PAUSE_COMMAND = 1
PLAYBACK_RESUME_COMMAND = 2
PLAYBACK_SEEK_COMMAND = 3
PLAYBACK_STREAM_BEGIN_COMMAND = 4
PLAYBACK_END_OF_FILE_COMMAND = 17


@dataclass(frozen=True, slots=True)
class PlaybackStreamBegin:
    start_ms: int
    end_ms: int | None


@dataclass(frozen=True, slots=True)
class PlaybackEndOfFile:
    file_id_ms: int
    error_code: int

    @property
    def succeeded(self) -> bool:
        return self.file_id_ms != 0 and self.error_code == 0


def parse_playback_stream_event(
    command: int,
    payload: bytes,
) -> PlaybackStreamBegin | PlaybackEndOfFile:
    """Decode only the command-4 BOF and command-17 EOF payloads consumed by the SDK player."""

    if type(command) is not int:
        raise ValueError("playback event command is invalid")
    if command == PLAYBACK_STREAM_BEGIN_COMMAND:
        if len(payload) not in (8, 16):
            raise ValueError("playback stream-begin payload size is invalid")
        start_ms = struct.unpack_from("<Q", payload)[0]
        end_ms = struct.unpack_from("<Q", payload, 8)[0] if len(payload) == 16 else None
        if start_ms == 0 or (end_ms is not None and end_ms <= start_ms):
            raise ValueError("playback stream-begin range is invalid")
        return PlaybackStreamBegin(start_ms, end_ms)
    if command == PLAYBACK_END_OF_FILE_COMMAND:
        if len(payload) not in (8, 16):
            raise ValueError("playback EOF payload size is invalid")
        file_id_ms = struct.unpack_from("<Q", payload)[0]
        error_code = struct.unpack_from("<Q", payload, 8)[0] if len(payload) == 16 else 0
        return PlaybackEndOfFile(file_id_ms, error_code)
    raise ValueError("unsupported playback lifecycle event")
