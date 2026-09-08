import struct

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_events import (
    PLAYBACK_END_OF_FILE_COMMAND,
    PLAYBACK_PAUSE_COMMAND,
    PLAYBACK_RESUME_COMMAND,
    PLAYBACK_SEEK_COMMAND,
    PLAYBACK_STREAM_BEGIN_COMMAND,
    PlaybackEndOfFile,
    PlaybackStreamBegin,
    parse_playback_stream_event,
)


def test_playback_control_and_event_command_ids_match_native_sdk():
    assert (
        PLAYBACK_PAUSE_COMMAND,
        PLAYBACK_RESUME_COMMAND,
        PLAYBACK_SEEK_COMMAND,
        PLAYBACK_STREAM_BEGIN_COMMAND,
        PLAYBACK_END_OF_FILE_COMMAND,
    ) == (1, 2, 3, 4, 17)


def test_parses_stream_begin_with_one_or_two_epoch_millisecond_fields():
    start_ms = 1_788_264_001_000
    end_ms = 1_788_264_031_000

    assert parse_playback_stream_event(4, struct.pack("<Q", start_ms)) == PlaybackStreamBegin(
        start_ms, None
    )
    assert parse_playback_stream_event(
        4, struct.pack("<QQ", start_ms, end_ms)
    ) == PlaybackStreamBegin(start_ms, end_ms)


def test_stream_begin_rejects_truncation_trailing_data_and_invalid_range():
    with pytest.raises(ValueError):
        parse_playback_stream_event(4, bytes(7))
    with pytest.raises(ValueError):
        parse_playback_stream_event(4, bytes(9))
    with pytest.raises(ValueError):
        parse_playback_stream_event(4, struct.pack("<Q", 0))
    with pytest.raises(ValueError):
        parse_playback_stream_event(4, struct.pack("<QQ", 100, 100))


def test_parses_success_and_error_eof_events():
    file_id = 1_788_264_001_000

    success = parse_playback_stream_event(17, struct.pack("<Q", file_id))
    failure = parse_playback_stream_event(17, struct.pack("<QQ", file_id, 7))
    missing = parse_playback_stream_event(17, struct.pack("<Q", 0))

    assert success == PlaybackEndOfFile(file_id, 0)
    assert success.succeeded is True
    assert failure == PlaybackEndOfFile(file_id, 7)
    assert failure.succeeded is False
    assert missing.succeeded is False


def test_event_parser_rejects_unknown_commands_and_invalid_eof_sizes():
    with pytest.raises(ValueError):
        parse_playback_stream_event(16, bytes(8))
    with pytest.raises(ValueError):
        parse_playback_stream_event(17, bytes(15))
    with pytest.raises(ValueError):
        parse_playback_stream_event(True, bytes(8))
