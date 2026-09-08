"""Socket-free codecs for bounded IoTVideo onboard-recording thumbnails."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

from .onboard_playback_modern import ModernPlaybackFile

PLAYBACK_THUMBNAIL_REQUEST_COMMAND: Final = 26
PLAYBACK_THUMBNAIL_CANCEL_COMMAND: Final = 27
PLAYBACK_THUMBNAIL_RESPONSE_HEADER_SIZE: Final = 28
PLAYBACK_THUMBNAIL_MAX_FILES: Final = 500
PLAYBACK_THUMBNAIL_MAX_BYTES: Final = 4 * 1024 * 1024
PLAYBACK_THUMBNAIL_MAX_CHUNK: Final = 0x7800


@dataclass(frozen=True, slots=True)
class PlaybackThumbnailChunk:
    error_code: int
    file_id_us: int
    total_size: int
    offset: int
    data: bytes

    @property
    def complete(self) -> bool:
        return self.error_code == 0 and self.offset + len(self.data) == self.total_size


def playback_file_id_us(file: ModernPlaybackFile) -> int:
    """Convert the public millisecond value back to the SDK's internal microsecond ID."""

    if not isinstance(file, ModernPlaybackFile) or not 0 < file.start_ms <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("playback-thumbnail file is invalid")
    value = file.start_ms * 1000
    if value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("playback-thumbnail file id overflows u64")
    return value


def build_playback_thumbnail_request(files: tuple[ModernPlaybackFile, ...]) -> bytes:
    """Build command 26's ``count + start_us[]`` request body."""

    if not isinstance(files, tuple) or not 1 <= len(files) <= PLAYBACK_THUMBNAIL_MAX_FILES:
        raise ValueError("playback-thumbnail file count is invalid")
    file_ids = tuple(playback_file_id_us(file) for file in files)
    if len(set(file_ids)) != len(file_ids):
        raise ValueError("playback-thumbnail files contain duplicate ids")
    return struct.pack(f"<I{len(file_ids)}Q", len(file_ids), *file_ids)


def unpack_playback_thumbnail_request(payload: bytes) -> tuple[int, ...]:
    """Decode a command-26 request for tests and diagnostics."""

    if len(payload) < 12 or (len(payload) - 4) % 8:
        raise ValueError("playback-thumbnail request size is invalid")
    count = struct.unpack_from("<I", payload)[0]
    if not 1 <= count <= PLAYBACK_THUMBNAIL_MAX_FILES or len(payload) != 4 + count * 8:
        raise ValueError("playback-thumbnail request count is inconsistent")
    file_ids = struct.unpack_from(f"<{count}Q", payload, 4)
    if any(file_id == 0 for file_id in file_ids) or len(set(file_ids)) != count:
        raise ValueError("playback-thumbnail request ids are invalid")
    return file_ids


def build_playback_thumbnail_cancel() -> bytes:
    """Return command 27's proven empty body."""

    return b""


def parse_playback_thumbnail_chunk(
    payload: bytes,
    *,
    expected_file_ids_us: frozenset[int],
) -> PlaybackThumbnailChunk:
    """Parse one response block while bounding allocation and file correlation."""

    if not expected_file_ids_us or len(expected_file_ids_us) > PLAYBACK_THUMBNAIL_MAX_FILES:
        raise ValueError("playback-thumbnail expected ids are invalid")
    if any(
        type(file_id) is not int or not 0 < file_id <= 0xFFFFFFFFFFFFFFFF
        for file_id in expected_file_ids_us
    ):
        raise ValueError("playback-thumbnail expected id is invalid")
    if len(payload) < PLAYBACK_THUMBNAIL_RESPONSE_HEADER_SIZE:
        raise ValueError("playback-thumbnail response is truncated")
    error_code, file_id_us, total_size, offset, chunk_size = struct.unpack_from("<QQIII", payload)
    if file_id_us not in expected_file_ids_us:
        raise ValueError("playback-thumbnail response belongs to another file")
    if chunk_size > PLAYBACK_THUMBNAIL_MAX_CHUNK:
        raise ValueError("playback-thumbnail chunk exceeds the transport bound")
    if len(payload) != PLAYBACK_THUMBNAIL_RESPONSE_HEADER_SIZE + chunk_size:
        raise ValueError("playback-thumbnail response size is inconsistent")
    if error_code:
        if total_size or offset or chunk_size:
            raise ValueError("playback-thumbnail error carries unexpected data")
    elif (
        total_size == 0
        or total_size > PLAYBACK_THUMBNAIL_MAX_BYTES
        or chunk_size == 0
        or offset > total_size
        or chunk_size > total_size - offset
    ):
        raise ValueError("playback-thumbnail data range is invalid")
    return PlaybackThumbnailChunk(
        error_code=error_code,
        file_id_us=file_id_us,
        total_size=total_size,
        offset=offset,
        data=bytes(payload[PLAYBACK_THUMBNAIL_RESPONSE_HEADER_SIZE:]),
    )
