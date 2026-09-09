from __future__ import annotations

import struct
from datetime import UTC, datetime

import pytest

from backend.app.drivers.contracts import OnboardRecordingQuery
from backend.app.drivers.yoosee.p2p import onboard_playback_session
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.onboard_playback_dates import ModernPlaybackDatePage
from backend.app.drivers.yoosee.p2p.onboard_playback_message import (
    build_onboard_playback_date_message,
    build_onboard_playback_list_message,
    build_onboard_playback_recording_types_message,
)
from backend.app.drivers.yoosee.p2p.onboard_playback_modern import ModernPlaybackPage
from backend.app.drivers.yoosee.p2p.onboard_playback_response import (
    parse_onboard_playback_date_response,
    parse_onboard_playback_list_response,
    parse_onboard_playback_recording_types_response,
)
from backend.app.drivers.yoosee.p2p.onboard_playback_types import (
    ModernPlaybackRecordingTypePage,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    build_builtin_command,
    parse_builtin_command,
)


def _query() -> OnboardRecordingQuery:
    return OnboardRecordingQuery(
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        limit=50,
    )


@pytest.mark.parametrize(
    ("protocol_version", "command"),
    ((1, 0), (2, 16), (3, 16), (4, 16)),
)
def test_builds_transport_neutral_playback_list_message(protocol_version: int, command: int):
    message = parse_builtin_command(
        build_onboard_playback_list_message(
            _query(),
            0xDEADBEEF,
            page_index=3,
            protocol_version=protocol_version,
        )
    )

    assert message.command == command
    assert message.flags == 0
    assert message.timestamp == 0xDEADBEEF
    assert message.payload[0] == protocol_version


def test_builds_exact_google_play_v2_native_page_size():
    message = parse_builtin_command(
        build_onboard_playback_list_message(
            _query(),
            0xDEADBEEF,
            protocol_version=2,
            count_per_page=500,
        )
    )

    assert struct.unpack_from(">I", message.payload, 21)[0] == 500


def test_rejects_page_size_override_for_uncertified_protocol():
    with pytest.raises(ValueError, match="only certified for playback V2"):
        build_onboard_playback_list_message(
            _query(),
            1,
            protocol_version=3,
            count_per_page=500,
        )


@pytest.mark.parametrize("protocol_version", (2, 3, 4))
def test_builds_transport_neutral_date_message(protocol_version: int):
    message = parse_builtin_command(
        build_onboard_playback_date_message(
            _query(),
            20,
            page_index=3,
            protocol_version=protocol_version,
        )
    )

    assert message.command == 18
    assert message.timestamp == 20
    assert message.payload[0] == protocol_version


@pytest.mark.parametrize("protocol_version", (3, 4))
def test_builds_transport_neutral_recording_type_message(protocol_version: int):
    message = parse_builtin_command(
        build_onboard_playback_recording_types_message(
            _query(),
            20,
            page_index=3,
            protocol_version=protocol_version,
        )
    )

    assert message.command == 15
    assert message.timestamp == 20
    assert message.payload[0] == protocol_version


def test_rejects_unrecovered_protocol_versions():
    with pytest.raises(ValueError):
        build_onboard_playback_list_message(_query(), 1, protocol_version=5)
    with pytest.raises(ValueError):
        build_onboard_playback_date_message(_query(), 1, protocol_version=1)
    with pytest.raises(ValueError):
        build_onboard_playback_recording_types_message(_query(), 1, protocol_version=2)


def _empty_v23_response(protocol_version: int) -> bytes:
    body = bytearray(26)
    body[0] = protocol_version
    struct.pack_into("<iIIIQ", body, 1, -1, 0, 0, 0, 1_788_264_000_000)
    return bytes(body)


def test_dispatches_correlated_response_without_requiring_command_echo():
    response = build_builtin_command(0, _empty_v23_response(2), timestamp_us=0x12345678)

    assert parse_onboard_playback_list_response(response, 0x12345678) == ModernPlaybackPage(
        0,
        0,
        -1,
        (),
    )


def test_dispatches_date_and_recording_type_responses_by_inner_request_id():
    date_response = build_builtin_command(0, _empty_v23_response(2), timestamp_us=11)
    type_response = build_builtin_command(0, _empty_v23_response(3), timestamp_us=12)

    assert parse_onboard_playback_date_response(date_response, 11) == ModernPlaybackDatePage(
        0,
        0,
        -1,
        (),
    )
    assert parse_onboard_playback_recording_types_response(
        type_response,
        12,
    ) == ModernPlaybackRecordingTypePage(0, 0, -1, ())


@pytest.mark.parametrize(
    "parser",
    (
        parse_onboard_playback_list_response,
        parse_onboard_playback_date_response,
        parse_onboard_playback_recording_types_response,
    ),
)
def test_rejects_response_with_different_inner_request_id(parser):
    response = build_builtin_command(0, _empty_v23_response(3), timestamp_us=7)

    with pytest.raises(ValueError, match="request ID does not match"):
        parser(response, 8, protocol_version=3)


def test_rejects_sdk_error_response_before_operation_parser():
    response = build_builtin_command(0xFF, bytes(8), timestamp_us=7)

    with pytest.raises(ValueError, match="SDK error response"):
        parse_onboard_playback_list_response(response, 7)


@pytest.mark.parametrize("request_id", (-1, 0x1_0000_0000, True))
def test_rejects_invalid_response_request_id(request_id):
    response = build_builtin_command(0, _empty_v23_response(2), timestamp_us=7)

    with pytest.raises(ValueError, match="request ID is invalid"):
        parse_onboard_playback_list_response(response, request_id)


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, value: bytes, peer: tuple[str, int]) -> None:
        self.sent.append((value, peer))


def _b9_response(message: bytes, *, access_id: int, device_id: int) -> bytes:
    response = bytearray(0x34 + len(message))
    response[:2] = b"\x7e\xb9"
    struct.pack_into("<H", response, 2, len(response))
    struct.pack_into("<I", response, 0x14, (2 << 16) | (1 << 18))
    struct.pack_into("<Q", response, 0x1C, access_id)
    struct.pack_into("<Q", response, 0x24, device_id)
    struct.pack_into("<I", response, 0x2C, 0x556677)
    struct.pack_into("<H", response, 0x30, len(message))
    response[0x34:] = message
    return bytes(response)


def test_certified_exchange_correlates_transport_peer_and_application_layers(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    device = OnlineDevice(7_443_576_841, 1, False, 1, bytes(16))
    sock = _FakeSocket()
    request_id = 0x11223344
    message_id = 0x123456
    transport_ack = bytearray(0x20)
    transport_ack[:2] = b"\x7e\xb9"
    struct.pack_into("<H", transport_ack, 2, len(transport_ack))
    struct.pack_into("<I", transport_ack, 0x0C, 18)
    struct.pack_into("<I", transport_ack, 0x14, 1 << 20)
    peer_receipt = bytearray(0x34)
    peer_receipt[:2] = b"\x7e\xba"
    struct.pack_into("<H", peer_receipt, 2, len(peer_receipt))
    struct.pack_into("<Q", peer_receipt, 0x1C, 123)
    struct.pack_into("<Q", peer_receipt, 0x24, device.device_id)
    struct.pack_into("<I", peer_receipt, 0x2C, message_id)
    response = _b9_response(
        build_builtin_command(0, _empty_v23_response(2), timestamp_us=request_id),
        access_id=123,
        device_id=device.device_id,
    )
    received = (
        (bytes(transport_ack), node.address),
        (bytes(peer_receipt), node.address),
        (response, node.address),
    )
    acknowledgements: list[bytes] = []

    monkeypatch.setattr(onboard_playback_session.secrets, "randbits", lambda _bits: request_id)
    monkeypatch.setattr(
        onboard_playback_session.secrets,
        "randbelow",
        lambda _upper: message_id - 1,
    )
    monkeypatch.setattr(
        onboard_playback_session,
        "receive_datagrams",
        lambda *_args: iter(received),
    )
    monkeypatch.setattr(onboard_playback_session, "decrypt_node_frame", lambda wire, _node: wire)
    monkeypatch.setattr(
        onboard_playback_session,
        "acknowledge_reliable_node_frame",
        lambda _sock, _node, frame: acknowledgements.append(frame) or True,
    )

    result = onboard_playback_session._exchange_certified_onboard_playback_list(
        sock,  # type: ignore[arg-type]
        node,
        123,
        device,
        _query(),
        18,
        0.5,
        retries=1,
    )

    assert result == onboard_playback_session.OnboardPlaybackListExchange(
        True,
        True,
        ModernPlaybackPage(0, 0, -1, ()),
    )
    assert acknowledgements == [bytes(peer_receipt), response]
    assert len(sock.sent) == 2
    assert sock.sent[0][1] == sock.sent[1][1] == node.address


def test_certified_exchange_ignores_uncorrelated_inner_response(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    device = OnlineDevice(7_443_576_841, 1, False, 1, bytes(16))
    response = _b9_response(
        build_builtin_command(0, _empty_v23_response(2), timestamp_us=99),
        access_id=123,
        device_id=device.device_id,
    )
    monkeypatch.setattr(onboard_playback_session.secrets, "randbits", lambda _bits: 100)
    monkeypatch.setattr(onboard_playback_session.secrets, "randbelow", lambda _upper: 0)
    monkeypatch.setattr(
        onboard_playback_session,
        "receive_datagrams",
        lambda *_args: iter(((response, node.address),)),
    )
    monkeypatch.setattr(onboard_playback_session, "decrypt_node_frame", lambda wire, _node: wire)

    result = onboard_playback_session._exchange_certified_onboard_playback_list(
        _FakeSocket(),  # type: ignore[arg-type]
        node,
        123,
        device,
        _query(),
        18,
        0.5,
        retries=1,
    )

    assert result.page is None


def test_certified_exchange_preserves_native_ten_second_response_window(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    device = OnlineDevice(7_443_576_841, 1, False, 1, bytes(16))
    receive_deadlines: list[float] = []

    monkeypatch.setattr(onboard_playback_session.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(onboard_playback_session.secrets, "randbits", lambda _bits: 1)
    monkeypatch.setattr(onboard_playback_session.secrets, "randbelow", lambda _upper: 0)
    monkeypatch.setattr(
        onboard_playback_session,
        "receive_datagrams",
        lambda _sock, deadline: receive_deadlines.append(deadline) or iter(()),
    )

    onboard_playback_session._exchange_certified_onboard_playback_list(
        _FakeSocket(),  # type: ignore[arg-type]
        node,
        123,
        device,
        _query(),
        18,
        30.0,
        retries=1,
        deadline=120.0,
    )

    assert receive_deadlines == [110.0]
