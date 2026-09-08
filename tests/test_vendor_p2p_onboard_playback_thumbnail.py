import struct

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_modern import ModernPlaybackFile
from backend.app.drivers.yoosee.p2p.onboard_playback_thumbnail import (
    PLAYBACK_THUMBNAIL_CANCEL_COMMAND,
    PLAYBACK_THUMBNAIL_MAX_BYTES,
    PLAYBACK_THUMBNAIL_MAX_CHUNK,
    PLAYBACK_THUMBNAIL_REQUEST_COMMAND,
    PlaybackThumbnailChunk,
    build_playback_thumbnail_cancel,
    build_playback_thumbnail_request,
    parse_playback_thumbnail_chunk,
    playback_file_id_us,
    unpack_playback_thumbnail_request,
)

FILE = ModernPlaybackFile(1_788_264_001_000, 1_788_264_031_000, 30_000, "motion")
FILE_ID_US = 1_788_264_001_000_000


def test_thumbnail_command_ids_and_request_layout_match_native_sdk():
    payload = build_playback_thumbnail_request((FILE,))

    assert (PLAYBACK_THUMBNAIL_REQUEST_COMMAND, PLAYBACK_THUMBNAIL_CANCEL_COMMAND) == (26, 27)
    assert playback_file_id_us(FILE) == FILE_ID_US
    assert payload == struct.pack("<IQ", 1, FILE_ID_US)
    assert unpack_playback_thumbnail_request(payload) == (FILE_ID_US,)
    assert build_playback_thumbnail_cancel() == b""


def test_thumbnail_request_rejects_empty_duplicate_or_overflowing_files():
    with pytest.raises(ValueError):
        build_playback_thumbnail_request(())
    with pytest.raises(ValueError):
        build_playback_thumbnail_request((FILE, FILE))
    with pytest.raises(ValueError):
        playback_file_id_us(ModernPlaybackFile(0xFFFFFFFFFFFFFFFF, 1, 1, "motion"))
    with pytest.raises(ValueError):
        unpack_playback_thumbnail_request(struct.pack("<IQ", 2, FILE_ID_US))


def _chunk(
    *,
    error: int = 0,
    file_id: int = FILE_ID_US,
    total: int = 10,
    offset: int = 4,
    data: bytes = b"567890",
) -> bytes:
    return struct.pack("<QQIII", error, file_id, total, offset, len(data)) + data


def test_parses_correlated_thumbnail_chunk_and_completion():
    parsed = parse_playback_thumbnail_chunk(_chunk(), expected_file_ids_us=frozenset({FILE_ID_US}))

    assert parsed == PlaybackThumbnailChunk(0, FILE_ID_US, 10, 4, b"567890")
    assert parsed.complete is True


def test_parses_content_free_device_error_for_the_expected_file():
    parsed = parse_playback_thumbnail_chunk(
        _chunk(error=0x56BB, total=0, offset=0, data=b""),
        expected_file_ids_us=frozenset({FILE_ID_US}),
    )

    assert parsed.error_code == 0x56BB
    assert parsed.complete is False


@pytest.mark.parametrize(
    "payload",
    [
        bytes(27),
        _chunk(file_id=FILE_ID_US + 1),
        _chunk(total=5),
        _chunk(total=PLAYBACK_THUMBNAIL_MAX_BYTES + 1),
        _chunk(error=1),
        _chunk(data=bytes(PLAYBACK_THUMBNAIL_MAX_CHUNK + 1)),
        _chunk() + b"x",
    ],
)
def test_thumbnail_response_fails_closed_on_untrusted_ranges(payload):
    with pytest.raises(ValueError):
        parse_playback_thumbnail_chunk(
            payload,
            expected_file_ids_us=frozenset({FILE_ID_US}),
        )


def test_thumbnail_response_rejects_invalid_expected_id_set():
    with pytest.raises(ValueError):
        parse_playback_thumbnail_chunk(_chunk(), expected_file_ids_us=frozenset())
    with pytest.raises(ValueError):
        parse_playback_thumbnail_chunk(_chunk(), expected_file_ids_us=frozenset({True}))
