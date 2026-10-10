import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_control_send import ReliableAvControl
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import (
    build_kcp_ack,
    build_kcp_push,
    build_mtp_frame,
    parse_kcp_segments,
    verify_mtp_frame,
)
from backend.app.drivers.yoosee.p2p.mtp_tcp_kcp import (
    unwrap_tcp_relay_kcp,
    wrap_tcp_relay_kcp,
)


@pytest.mark.parametrize("conversation", [7, 0x80000007])
@pytest.mark.parametrize("channel,prefix,offset", [(0x85, 0, 6), (0x86, 0x60, 14)])
def test_outbound_sdk_envelope_preserves_kcp_bytes(conversation, channel, prefix, offset):
    canonical = build_kcp_push(conversation, 9, b"synthetic", timestamp=123)
    wire = wrap_tcp_relay_kcp(canonical, destination_id=23,
                              expected_conversation=conversation, channel_type=channel)
    assert wire[:2] == bytes((0xC0, prefix))
    assert verify_mtp_frame(wire)
    assert wire[offset:] == canonical[6:]
    if offset == 14:
        assert wire[6:14] == struct.pack("<Q", 23)
    assert wire == wrap_tcp_relay_kcp(canonical, destination_id=23,
                                      expected_conversation=conversation, channel_type=channel)


@pytest.mark.parametrize("prefix", [0x10, 0x50])
def test_inbound_normalization_preserves_all_coalesced_segments(prefix):
    first = build_kcp_push(7, 1, b"one", timestamp=3)
    second = build_kcp_push(7, 2, b"two", timestamp=4)
    body = first[6:] + second[6:]
    wire = build_mtp_frame(prefix, (b"opaque!!" if prefix == 0x50 else b"") + body)
    result = unwrap_tcp_relay_kcp(wire, expected_conversation=7)
    assert result == build_mtp_frame(0x10, body)
    assert len(parse_kcp_segments(result)) == 2


def test_original_checksum_checked_before_normalizing():
    body = build_kcp_push(7, 0, b"test", timestamp=1)[6:]
    wire = build_mtp_frame(0x50, b"opaque!!" + body)
    for offset in range(30):
        corrupt = bytearray(wire)
        corrupt[offset] ^= 1
        with pytest.raises(ValueError):
            unwrap_tcp_relay_kcp(bytes(corrupt), expected_conversation=7)
    for length in range(len(wire)):
        with pytest.raises(ValueError):
            unwrap_tcp_relay_kcp(wire[:length], expected_conversation=7)


@pytest.mark.parametrize("prefix", [0, 0x60, 0x90, 0xD0, 0xE0, 0x30, 0x70])
def test_outbound_or_unmapped_prefix_is_not_inbound(prefix):
    frame = build_mtp_frame(prefix, bytes(40))
    with pytest.raises(ValueError):
        unwrap_tcp_relay_kcp(frame, expected_conversation=7)


@pytest.mark.parametrize("bad", [True, -1, 1 << 32, 7.0, 8])
def test_expected_conversation_is_exact(bad):
    frame = build_kcp_push(7, 0, b"test", timestamp=1)
    with pytest.raises(ValueError):
        wrap_tcp_relay_kcp(frame, destination_id=23, expected_conversation=bad)
    with pytest.raises(ValueError):
        unwrap_tcp_relay_kcp(frame, expected_conversation=bad)


def test_mixed_conversations_in_coalesced_record_fail_closed():
    payload = build_kcp_push(7, 0, b"one")[6:] + build_kcp_push(8, 0, b"two")[6:]
    with pytest.raises(ValueError):
        wrap_tcp_relay_kcp(build_mtp_frame(0x10, payload), destination_id=23,
                           expected_conversation=7)
    with pytest.raises(ValueError):
        unwrap_tcp_relay_kcp(build_mtp_frame(0x50, b"opaque!!" + payload), expected_conversation=7)


@pytest.mark.parametrize("size", [0, 1, 23, 25])
def test_checksum_valid_malformed_kcp_payload_rejected(size):
    payload = build_kcp_push(7, 0, b"test")[6:6 + size]
    with pytest.raises(ValueError):
        unwrap_tcp_relay_kcp(build_mtp_frame(0x50, b"opaque!!" + payload), expected_conversation=7)


def test_tcp_limit_applies_after_route_prefix_is_added():
    canonical = build_kcp_push(7, 0, bytes(1462), timestamp=1)
    assert len(wrap_tcp_relay_kcp(canonical, destination_id=23, expected_conversation=7)) == 1500
    too_big = build_kcp_push(7, 0, bytes(1463), timestamp=1)
    with pytest.raises(ValueError):
        wrap_tcp_relay_kcp(too_big, destination_id=23, expected_conversation=7)
    with pytest.raises(ValueError):
        unwrap_tcp_relay_kcp(build_mtp_frame(0x50, b"opaque!!" + too_big[6:]),
                             expected_conversation=7)


@pytest.mark.parametrize("destination", [True, -1, 1 << 64, 23.0])
def test_invalid_destination(destination):
    with pytest.raises(ValueError):
        wrap_tcp_relay_kcp(build_kcp_push(7, 0, b"test"), destination_id=destination,
                           expected_conversation=7)


@pytest.mark.parametrize("channel", [True, 0x86 + 0.0, 0x87, 3])
def test_unmapped_channel(channel):
    with pytest.raises(ValueError):
        wrap_tcp_relay_kcp(build_kcp_push(7, 0, b"test"), destination_id=23,
                           expected_conversation=7, channel_type=channel)


@pytest.mark.parametrize("action,sequence,conversation", [(1, 0, 0x80000007), (6, 0, 7), (7, 1, 7)])
def test_existing_control_sender_composes_without_changing_receipt_ownership(
    action, sequence, conversation,
):
    peer = ("192.0.2.1", 1234)
    now = [1.0]
    sender = ReliableAvControl(peer, 7, 42, action=action, sequence=sequence, clock=lambda: now[0])
    canonical = sender.due()
    outbound = wrap_tcp_relay_kcp(canonical, destination_id=23, expected_conversation=conversation)
    assert outbound[14:] == canonical[6:]
    now[0] += 0.25
    assert wrap_tcp_relay_kcp(sender.due(), destination_id=23,
                              expected_conversation=conversation) == outbound
    stale = build_kcp_ack(conversation, sequence, 999, unacknowledged=sequence + 1)
    normalized = unwrap_tcp_relay_kcp(build_mtp_frame(0x50, b"opaque!!" + stale[6:]),
                                      expected_conversation=conversation)
    assert not sender.receive_ack(normalized, peer)
    receipt = build_kcp_ack(conversation, sequence, 1000, unacknowledged=sequence + 1)
    normalized = unwrap_tcp_relay_kcp(build_mtp_frame(0x50, b"opaque!!" + receipt[6:]),
                                      expected_conversation=conversation)
    assert not sender.receive_ack(normalized, (peer[0], 1235))
    assert sender.receive_ack(normalized, peer)
    sender.close()
    with pytest.raises(ReceiveError):
        sender.receive_ack(normalized, peer)
