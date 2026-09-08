import struct

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_download import (
    PLAYBACK_DOWNLOAD_CANCEL_COMMAND,
    PLAYBACK_DOWNLOAD_DATA_COMMAND,
    PLAYBACK_DOWNLOAD_ERROR_COMMAND,
    PLAYBACK_DOWNLOAD_INFO_COMMAND,
    PLAYBACK_DOWNLOAD_MAX_DATA_CHUNK,
    PLAYBACK_DOWNLOAD_REQUEST_COMMAND,
    PlaybackDownloadError,
    PlaybackDownloadInfo,
    build_playback_download_cancel,
    build_playback_download_request,
    parse_playback_download_error,
    parse_playback_download_info,
    unpack_playback_download_request,
    validate_playback_download_data,
)
from backend.app.drivers.yoosee.p2p.onboard_playback_modern import ModernPlaybackFile

FILE = ModernPlaybackFile(1_788_264_001_000, 1_788_264_031_000, 30_000, "motion")


def test_download_command_ids_and_request_layout_match_native_sdk():
    assert (
        PLAYBACK_DOWNLOAD_INFO_COMMAND,
        PLAYBACK_DOWNLOAD_CANCEL_COMMAND,
        PLAYBACK_DOWNLOAD_REQUEST_COMMAND,
        PLAYBACK_DOWNLOAD_ERROR_COMMAND,
        PLAYBACK_DOWNLOAD_DATA_COMMAND,
    ) == (19, 20, 21, 22, 23)

    payload = build_playback_download_request(FILE, offset=4096)

    assert payload == struct.pack("<QI", FILE.start_ms, 4096)
    assert unpack_playback_download_request(payload) == (FILE.start_ms, 4096)
    assert build_playback_download_cancel() == b""


@pytest.mark.parametrize("offset", [-1, 0x1_0000_0000, True])
def test_download_request_rejects_invalid_offsets(offset):
    with pytest.raises(ValueError):
        build_playback_download_request(FILE, offset=offset)


def test_download_request_rejects_invalid_file_or_identifier():
    with pytest.raises(ValueError):
        build_playback_download_request(object())
    with pytest.raises(ValueError):
        build_playback_download_request(ModernPlaybackFile(0, 1, 1, "motion"))
    with pytest.raises(ValueError):
        unpack_playback_download_request(bytes(11))


def _info_payload(
    *,
    file_id: int = FILE.start_ms,
    total_size: int = 10_000,
    remaining_size: int = 5904,
    name: bytes = b"recording.mp4",
) -> bytes:
    return struct.pack("<QIIH", file_id, total_size, remaining_size, len(name)) + name


def test_parses_correlated_download_info_and_resume_size():
    assert parse_playback_download_info(
        _info_payload(),
        expected_file_id=FILE.start_ms,
        requested_offset=4096,
    ) == PlaybackDownloadInfo(FILE.start_ms, 10_000, 5904, "recording.mp4")


@pytest.mark.parametrize(
    "payload,offset",
    [
        (bytes(17), 0),
        (_info_payload(file_id=FILE.start_ms + 1), 4096),
        (_info_payload(remaining_size=5903), 4096),
        (_info_payload(name=b""), 4096),
        (_info_payload(name=b"x" * 23), 4096),
        (_info_payload(name=b"../escape.mp4"), 4096),
        (_info_payload(name=b"dir/file.mp4"), 4096),
        (_info_payload(name=b"bad\xff.mp4"), 4096),
        (_info_payload() + b"x", 4096),
    ],
)
def test_download_info_fails_closed_on_untrusted_metadata(payload, offset):
    with pytest.raises(ValueError):
        parse_playback_download_info(
            payload,
            expected_file_id=FILE.start_ms,
            requested_offset=offset,
        )


def test_parses_only_correlated_fixed_size_download_errors():
    payload = struct.pack("<QI", FILE.start_ms, 7)

    assert parse_playback_download_error(
        payload, expected_file_id=FILE.start_ms
    ) == PlaybackDownloadError(FILE.start_ms, 7)
    with pytest.raises(ValueError):
        parse_playback_download_error(payload + b"x", expected_file_id=FILE.start_ms)
    with pytest.raises(ValueError):
        parse_playback_download_error(payload, expected_file_id=FILE.start_ms + 1)


def test_validates_each_data_chunk_without_accumulating_it():
    payload = b"chunk"

    assert validate_playback_download_data(payload, remaining_size=5) == payload
    with pytest.raises(ValueError):
        validate_playback_download_data(b"", remaining_size=5)
    with pytest.raises(ValueError):
        validate_playback_download_data(b"123456", remaining_size=5)
    with pytest.raises(ValueError):
        validate_playback_download_data(
            bytes(PLAYBACK_DOWNLOAD_MAX_DATA_CHUNK + 1),
            remaining_size=PLAYBACK_DOWNLOAD_MAX_DATA_CHUNK + 1,
        )
