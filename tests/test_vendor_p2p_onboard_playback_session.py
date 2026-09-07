from __future__ import annotations

import struct
from datetime import UTC, datetime

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.onboard_playback_carrier import (
    build_onboard_playback_date_request,
    build_onboard_playback_list_request,
    build_onboard_playback_recording_types_request,
    parse_onboard_playback_date_response,
    parse_onboard_playback_list_response,
    parse_onboard_playback_recording_types_response,
)
from backend.app.drivers.yoosee.p2p.onboard_playback_dates import ModernPlaybackDatePage
from backend.app.drivers.yoosee.p2p.onboard_playback_modern import ModernPlaybackPage
from backend.app.drivers.yoosee.p2p.onboard_playback_types import (
    ModernPlaybackRecordingTypePage,
)


def _query() -> OnboardRecordingQuery:
    return OnboardRecordingQuery(
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        limit=50,
    )


def test_wraps_command_16_in_recovered_builtin_b9_envelope():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)

    frame = gute_mode2_decrypt(
        build_onboard_playback_list_request(
            node,
            123,
            7_000_000_002,
            _query(),
            18,
            19,
            20,
            page_index=3,
        ),
        node.session_key,
    )

    assert frame[:2] == b"\x7e\xb9"
    assert struct.unpack_from("<Q", frame, 0x1C)[0] == 7_000_000_002
    assert struct.unpack_from("<Q", frame, 0x24)[0] == 123
    assert frame[0x34:0x3C] == b"\x00\x10\x00\x00" + struct.pack("<I", 20)
    assert len(frame[0x3C:]) == 43
    assert frame[0x3C] == 2


def test_wraps_v1_in_builtin_command_zero_without_exposing_other_commands():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    frame = gute_mode2_decrypt(
        build_onboard_playback_list_request(
            node,
            123,
            7_000_000_002,
            _query(),
            18,
            19,
            20,
            protocol_version=1,
        ),
        node.session_key,
    )

    assert frame[0x34:0x3C] == b"\x00\x00\x00\x00" + struct.pack("<I", 20)
    assert frame[0x3C] == 1


def test_wraps_v3_and_v4_in_builtin_command_16_with_exact_protocol_byte():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)

    for protocol_version in (3, 4):
        frame = gute_mode2_decrypt(
            build_onboard_playback_list_request(
                node,
                123,
                7_000_000_002,
                _query(),
                18,
                19,
                20,
                protocol_version=protocol_version,
            ),
            node.session_key,
        )
        assert frame[0x34:0x3C] == b"\x00\x10\x00\x00" + struct.pack("<I", 20)
        assert frame[0x3C] == protocol_version


def test_wraps_date_query_in_builtin_command_18_for_v2_through_v4():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)

    for protocol_version in (2, 3, 4):
        frame = gute_mode2_decrypt(
            build_onboard_playback_date_request(
                node,
                123,
                7_000_000_002,
                _query(),
                18,
                19,
                20,
                protocol_version=protocol_version,
            ),
            node.session_key,
        )
        assert frame[0x34:0x3C] == b"\x00\x12\x00\x00" + struct.pack("<I", 20)
        assert frame[0x3C] == protocol_version


def test_wraps_recording_type_query_in_internal_command_15_for_v3_and_v4():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)

    for protocol_version in (3, 4):
        frame = gute_mode2_decrypt(
            build_onboard_playback_recording_types_request(
                node,
                123,
                7_000_000_002,
                _query(),
                18,
                19,
                20,
                protocol_version=protocol_version,
            ),
            node.session_key,
        )
        assert frame[0x34:0x3C] == b"\x00\x0f\x00\x00" + struct.pack("<I", 20)
        assert frame[0x3C] == protocol_version


def _empty_response(request_id: int) -> bytes:
    body = bytearray(26)
    body[0] = 2
    body[1:5] = (-1).to_bytes(4, "little", signed=True)
    payload = b"\x00\x10\x00\x00" + struct.pack("<I", request_id) + body
    response = bytearray(0x34 + len(payload))
    response[:2] = b"\x7e\xb9"
    struct.pack_into("<H", response, 0x30, len(payload))
    response[0x34:] = payload
    return bytes(response)


def _empty_date_response(request_id: int) -> bytes:
    response = bytearray(_empty_response(request_id))
    response[0x35] = 18
    return bytes(response)


def _empty_recording_types_response(request_id: int) -> bytes:
    response = bytearray(_empty_response(request_id))
    response[0x35] = 15
    response[0x3C] = 3
    return bytes(response)


def test_response_requires_exact_builtin_command_and_request_correlation():
    response = _empty_response(44)

    assert parse_onboard_playback_list_response(
        response,
        request_id=44,
    ) == ModernPlaybackPage(0, 0, -1, ())
    assert parse_onboard_playback_list_response(response, request_id=45) is None

    wrong_command = bytearray(response)
    wrong_command[0x35] = 18
    assert parse_onboard_playback_list_response(bytes(wrong_command), request_id=44) is None

    malformed_body = bytearray(response)
    malformed_body[-1] = 1
    assert parse_onboard_playback_list_response(bytes(malformed_body), request_id=44) is None


def test_date_response_requires_command_18_and_request_correlation():
    response = _empty_date_response(44)

    assert parse_onboard_playback_date_response(
        response,
        request_id=44,
    ) == ModernPlaybackDatePage(0, 0, -1, ())
    assert parse_onboard_playback_date_response(response, request_id=45) is None

    wrong_command = bytearray(response)
    wrong_command[0x35] = 16
    assert parse_onboard_playback_date_response(bytes(wrong_command), request_id=44) is None


def test_recording_type_response_requires_command_15_and_request_correlation():
    response = _empty_recording_types_response(44)

    assert parse_onboard_playback_recording_types_response(
        response,
        request_id=44,
    ) == ModernPlaybackRecordingTypePage(0, 0, -1, ())
    assert parse_onboard_playback_recording_types_response(response, request_id=45) is None

    wrong_command = bytearray(response)
    wrong_command[0x35] = 16
    assert (
        parse_onboard_playback_recording_types_response(bytes(wrong_command), request_id=44)
        is None
    )
