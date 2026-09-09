from __future__ import annotations

import struct

import pytest

from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.crypto import gute_mode1_decrypt, gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.onboard_playback_carrier import (
    build_onboard_playback_carrier,
    build_onboard_playback_lan_carrier,
    build_onboard_playback_receipt,
    is_onboard_playback_peer_receipt,
    is_onboard_playback_transport_ack,
    unwrap_onboard_playback_carrier,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import build_builtin_command

NODE = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
ACCESS_ID = 123
DEVICE_ID = 7_443_576_841


def test_read_only_playback_message_matches_native_b9_layout() -> None:
    message = build_builtin_command(16, b"list", timestamp_us=0x11223344)

    plain = gute_mode2_decrypt(
        build_onboard_playback_carrier(NODE, ACCESS_ID, DEVICE_ID, 18, 0x123456, message),
        NODE.session_key,
    )

    assert plain[:2] == b"\x7e\xb9"
    assert len(plain) == 0x34 + len(message)
    assert struct.unpack_from("<Q", plain, 4)[0] == NODE.session_id
    assert struct.unpack_from("<I", plain, 0x0C)[0] == 18
    flags = struct.unpack_from("<I", plain, 0x14)[0]
    assert (flags >> 16) & 3 == 2
    assert (flags >> 18) & 3 == 1
    assert struct.unpack_from("<I", plain, 0x18)[0] == 2
    assert struct.unpack_from("<Q", plain, 0x1C)[0] == DEVICE_ID
    assert struct.unpack_from("<Q", plain, 0x24)[0] == ACCESS_ID
    assert struct.unpack_from("<I", plain, 0x2C)[0] == 0x123456
    assert struct.unpack_from("<H", plain, 0x30)[0] == len(message)
    assert plain[0x34:] == message


def test_read_only_playback_message_matches_native_known_lan_copy() -> None:
    message = build_builtin_command(16, b"list", timestamp_us=0x11223344)

    plain = gute_mode1_decrypt(
        build_onboard_playback_lan_carrier(
            ACCESS_ID,
            DEVICE_ID,
            18,
            0x123456,
            message,
        )
    )

    assert plain[:2] == b"\x7e\xb9"
    assert struct.unpack_from("<Q", plain, 4)[0] == ACCESS_ID
    flags = struct.unpack_from("<I", plain, 0x14)[0]
    assert (flags >> 16) & 3 == 1
    assert (flags >> 18) & 3 == 1
    assert flags & (1 << 25)
    assert struct.unpack_from("<I", plain, 0x18)[0] == 2
    assert struct.unpack_from("<Q", plain, 0x1C)[0] == DEVICE_ID
    assert struct.unpack_from("<Q", plain, 0x24)[0] == ACCESS_ID
    assert plain[0x34:] == message


def test_response_carrier_requires_selected_identities_and_application_frame() -> None:
    message = build_builtin_command(16, b"page", timestamp_us=7)
    response = bytearray(0x34 + len(message))
    response[:2] = b"\x7e\xb9"
    struct.pack_into("<H", response, 2, len(response))
    struct.pack_into("<Q", response, 0x1C, ACCESS_ID)
    struct.pack_into("<Q", response, 0x24, DEVICE_ID)
    struct.pack_into("<H", response, 0x30, len(message))
    response[0x34:] = message

    assert (
        unwrap_onboard_playback_carrier(
            bytes(response),
            expected_access_id=ACCESS_ID,
            expected_device_id=DEVICE_ID,
        )
        == message
    )

    struct.pack_into("<I", response, 0x14, 1 << 20)
    with pytest.raises(ValueError, match="receipt"):
        unwrap_onboard_playback_carrier(
            bytes(response),
            expected_access_id=ACCESS_ID,
            expected_device_id=DEVICE_ID,
        )


def test_matches_only_the_correlated_reliable_transport_ack() -> None:
    ack = bytearray(0x20)
    ack[:2] = b"\x7e\xb9"
    struct.pack_into("<H", ack, 2, len(ack))
    struct.pack_into("<I", ack, 0x0C, 18)
    struct.pack_into("<I", ack, 0x14, 1 << 20)

    assert is_onboard_playback_transport_ack(bytes(ack), expected_sequence=18) is True
    assert is_onboard_playback_transport_ack(bytes(ack), expected_sequence=19) is False
    ack[1] = 0xBA
    assert is_onboard_playback_transport_ack(bytes(ack), expected_sequence=18) is False


def test_matches_only_the_correlated_full_peer_receipt() -> None:
    receipt = bytearray(0x34)
    receipt[:2] = b"\x7e\xba"
    struct.pack_into("<H", receipt, 2, len(receipt))
    struct.pack_into("<Q", receipt, 0x1C, ACCESS_ID)
    struct.pack_into("<Q", receipt, 0x24, DEVICE_ID)
    struct.pack_into("<I", receipt, 0x2C, 0x123456)

    assert is_onboard_playback_peer_receipt(
        bytes(receipt),
        expected_access_id=ACCESS_ID,
        expected_device_id=DEVICE_ID,
        expected_message_id=0x123456,
    )
    assert not is_onboard_playback_peer_receipt(
        bytes(receipt),
        expected_access_id=ACCESS_ID,
        expected_device_id=DEVICE_ID,
        expected_message_id=0x123457,
    )


def test_builds_ba_receipt_by_reversing_validated_response_identities() -> None:
    message = build_builtin_command(0, b"page", timestamp_us=7)
    response = bytearray(0x34 + len(message))
    response[:2] = b"\x7e\xb9"
    struct.pack_into("<H", response, 2, len(response))
    struct.pack_into("<I", response, 0x14, (2 << 16) | (1 << 18))
    struct.pack_into("<Q", response, 0x1C, ACCESS_ID)
    struct.pack_into("<Q", response, 0x24, DEVICE_ID)
    struct.pack_into("<I", response, 0x2C, 0x123456)
    struct.pack_into("<H", response, 0x30, len(message))
    response[0x34:] = message

    receipt = gute_mode2_decrypt(
        build_onboard_playback_receipt(
            NODE,
            bytes(response),
            19,
            expected_access_id=ACCESS_ID,
            expected_device_id=DEVICE_ID,
        ),
        NODE.session_key,
    )

    assert receipt[:2] == b"\x7e\xba"
    assert len(receipt) == 0x34
    assert struct.unpack_from("<I", receipt, 0x0C)[0] == 19
    assert struct.unpack_from("<Q", receipt, 0x1C)[0] == DEVICE_ID
    assert struct.unpack_from("<Q", receipt, 0x24)[0] == ACCESS_ID
    assert struct.unpack_from("<I", receipt, 0x2C)[0] == 0x123456


@pytest.mark.parametrize("command", [3, 4, 17, 19, 24, 28, 29, 0xFF])
def test_carrier_rejects_lifecycle_download_delete_and_error_commands(command) -> None:
    with pytest.raises(ValueError, match="read-only allowlisted"):
        build_onboard_playback_carrier(
            NODE,
            ACCESS_ID,
            DEVICE_ID,
            18,
            1,
            build_builtin_command(command),
        )
