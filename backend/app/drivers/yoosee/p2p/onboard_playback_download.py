"""Socket-free codecs for bounded IoTVideo onboard-file downloads."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .onboard_playback_modern import ModernPlaybackFile

PLAYBACK_DOWNLOAD_INFO_COMMAND = 19
PLAYBACK_DOWNLOAD_CANCEL_COMMAND = 20
PLAYBACK_DOWNLOAD_REQUEST_COMMAND = 21
PLAYBACK_DOWNLOAD_ERROR_COMMAND = 22
PLAYBACK_DOWNLOAD_DATA_COMMAND = 23

PLAYBACK_DOWNLOAD_REQUEST_SIZE = 12
PLAYBACK_DOWNLOAD_INFO_HEADER_SIZE = 18
PLAYBACK_DOWNLOAD_ERROR_SIZE = 12
PLAYBACK_DOWNLOAD_MAX_NAME_BYTES = 22
PLAYBACK_DOWNLOAD_MAX_DATA_CHUNK = 0x7800


@dataclass(frozen=True, slots=True)
class PlaybackDownloadInfo:
    file_id: int
    total_size: int
    remaining_size: int
    file_name: str


@dataclass(frozen=True, slots=True)
class PlaybackDownloadError:
    file_id: int
    error_code: int


def _validate_file_id(file_id: int) -> None:
    if type(file_id) is not int or not 0 < file_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("playback-download file id is invalid")


def _validate_offset(offset: int) -> None:
    if type(offset) is not int or not 0 <= offset <= 0xFFFFFFFF:
        raise ValueError("playback-download offset is invalid")


def build_playback_download_request(file: ModernPlaybackFile, *, offset: int = 0) -> bytes:
    """Build command 21's ``file start-ms + byte offset`` request body."""

    if not isinstance(file, ModernPlaybackFile):
        raise ValueError("playback-download file is invalid")
    _validate_file_id(file.start_ms)
    _validate_offset(offset)
    return struct.pack("<QI", file.start_ms, offset)


def unpack_playback_download_request(payload: bytes) -> tuple[int, int]:
    """Decode a command 21 body for tests and diagnostics."""

    if len(payload) != PLAYBACK_DOWNLOAD_REQUEST_SIZE:
        raise ValueError("playback-download request size is invalid")
    file_id, offset = struct.unpack("<QI", payload)
    _validate_file_id(file_id)
    return file_id, offset


def build_playback_download_cancel() -> bytes:
    """Return command 20's proven empty body."""

    return b""


def _decode_safe_file_name(value: bytes) -> str:
    if not value or len(value) > PLAYBACK_DOWNLOAD_MAX_NAME_BYTES or b"\x00" in value:
        raise ValueError("playback-download file name is invalid")
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("playback-download file name is not UTF-8") from exc
    if decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
        raise ValueError("playback-download file name is unsafe")
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        raise ValueError("playback-download file name contains control characters")
    return decoded


def parse_playback_download_info(
    payload: bytes,
    *,
    expected_file_id: int,
    requested_offset: int,
) -> PlaybackDownloadInfo:
    """Parse command 19 and enforce the SDK's size/offset consistency check."""

    _validate_file_id(expected_file_id)
    _validate_offset(requested_offset)
    if len(payload) < PLAYBACK_DOWNLOAD_INFO_HEADER_SIZE:
        raise ValueError("playback-download info is truncated")
    file_id, total_size, remaining_size, name_size = struct.unpack_from("<QIIH", payload)
    if file_id != expected_file_id:
        raise ValueError("playback-download info belongs to another file")
    if name_size == 0 or name_size > PLAYBACK_DOWNLOAD_MAX_NAME_BYTES:
        raise ValueError("playback-download file name size is invalid")
    if len(payload) != PLAYBACK_DOWNLOAD_INFO_HEADER_SIZE + name_size:
        raise ValueError("playback-download info size is inconsistent")
    if total_size == 0 or requested_offset > total_size:
        raise ValueError("playback-download total size is invalid")
    if total_size != requested_offset + remaining_size:
        raise ValueError("playback-download remaining size is inconsistent")
    return PlaybackDownloadInfo(
        file_id=file_id,
        total_size=total_size,
        remaining_size=remaining_size,
        file_name=_decode_safe_file_name(payload[PLAYBACK_DOWNLOAD_INFO_HEADER_SIZE:]),
    )


def parse_playback_download_error(
    payload: bytes,
    *,
    expected_file_id: int,
) -> PlaybackDownloadError:
    """Parse command 22 without accepting an error for a different file."""

    _validate_file_id(expected_file_id)
    if len(payload) != PLAYBACK_DOWNLOAD_ERROR_SIZE:
        raise ValueError("playback-download error size is invalid")
    file_id, error_code = struct.unpack("<QI", payload)
    if file_id != expected_file_id:
        raise ValueError("playback-download error belongs to another file")
    return PlaybackDownloadError(file_id, error_code)


def validate_playback_download_data(payload: bytes, *, remaining_size: int) -> bytes:
    """Validate one command-23 chunk without retaining or joining prior chunks."""

    if type(remaining_size) is not int or not 0 < remaining_size <= 0xFFFFFFFF:
        raise ValueError("playback-download remaining size is invalid")
    if not payload or len(payload) > PLAYBACK_DOWNLOAD_MAX_DATA_CHUNK:
        raise ValueError("playback-download data chunk size is invalid")
    if len(payload) > remaining_size:
        raise ValueError("playback-download data exceeds the advertised size")
    return bytes(payload)
