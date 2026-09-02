from datetime import UTC, datetime

import pytest

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p.onboard_playback_modern import (
    PLAYBACK_GET_LIST_V2_COMMAND,
    build_modern_playback_list_v2_request,
    unpack_modern_playback_list_v2_request,
)


def _query(limit: int = 50) -> OnboardRecordingQuery:
    return OnboardRecordingQuery(
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        limit=limit,
    )


def test_builds_recovered_v2_payload_for_builtin_command_16():
    payload = build_modern_playback_list_v2_request(_query(), page_index=3, filter_type="motion")

    assert PLAYBACK_GET_LIST_V2_COMMAND == 16
    assert len(payload) == 43
    assert payload.hex() == (
        "02"
        "002ad75ca0010000"
        "80180e5da0010000"
        "00000003"
        "00000032"
        "6d6f74696f6e000000000000000000000000"
    )
    assert unpack_modern_playback_list_v2_request(payload) == {
        "start_ms": 1_788_264_000_000,
        "end_ms": 1_788_267_600_000,
        "page_index": 3,
        "count_per_page": 50,
        "filter_type": "motion",
    }


def test_rejects_unrecovered_or_unsafe_v2_fields():
    with pytest.raises(ValueError):
        build_modern_playback_list_v2_request(_query(), page_index=-1)
    with pytest.raises(ValueError):
        build_modern_playback_list_v2_request(_query(), filter_type="áudio")
    with pytest.raises(ValueError):
        build_modern_playback_list_v2_request(_query(), filter_type="x" * 18)
    with pytest.raises(ValueError):
        unpack_modern_playback_list_v2_request(bytes(43))


def test_decoder_rejects_nonzero_reserved_filter_tail():
    payload = bytearray(build_modern_playback_list_v2_request(_query(), filter_type="A"))
    payload[-1] = 1

    with pytest.raises(ValueError):
        unpack_modern_playback_list_v2_request(bytes(payload))
