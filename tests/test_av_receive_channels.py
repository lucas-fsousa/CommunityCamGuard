import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_receive import AvReceiver
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import parse_kcp_segments
from tests.test_av_receive import COOKIE, media
from tests.test_captured_media import control, header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av

CONTROL = 42 | 0x80000000


def receiver(**kwargs):
    return AvReceiver(PEER, 42, 123, COOKIE, control_conv=CONTROL, **kwargs)


def start():
    body = bytearray(control(123))
    struct.pack_into("<I", body, 8, 6)
    return bytes(body)


def test_independent_sequence_zero_and_media_reordering():
    session = receiver()
    accepted = session.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    ack = parse_kcp_segments(accepted.acknowledgements[0])[0]
    assert (ack.conv, ack.unacknowledged) == (CONTROL, 1)
    encoded = media(header() + av())
    tail = session.receive(packet(2, 0, encoded[20:]), PEER)
    assert parse_kcp_segments(tail.acknowledgements[0])[0].unacknowledged == 0
    session.receive(packet(0, 0, start()), PEER)
    result = session.receive(packet(1, 1, encoded[:20]), PEER)
    assert session.phase == "active" and result.records[1].video == b"video"
    assert parse_kcp_segments(result.acknowledgements[0])[0].unacknowledged == 3
    assert not session.receive(packet(0, 0, control(123), conv=CONTROL), PEER).records
    assert session.buffered_bytes == 0


@pytest.mark.parametrize("case", ["start_before_accept", "media_before_start", "accept_on_media",
                                   "start_on_control", "media_on_control", "wrong_call"])
def test_channels_cannot_substitute_for_each_other(case):
    session = receiver()
    with pytest.raises(ReceiveError):
        if case != "start_before_accept":
            session.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
        body, conv, seq = start(), 42, 0
        if case == "media_before_start":
            body = media(header())
        elif case == "accept_on_media":
            body = control(123)
        elif case in ("start_on_control", "media_on_control", "wrong_call"):
            conv, seq = CONTROL, 1
            body = {"start_on_control": start(), "media_on_control": media(header()),
                    "wrong_call": control(124)}[case]
        session.receive(packet(seq, 0, body, conv=conv), PEER)
    assert session.phase == "closed" and session.buffered_bytes == 0


def test_quiet_control_channel_does_not_expire_active_media():
    now = [0.0]
    session = receiver(clock=lambda: now[0])
    session.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    session.receive(packet(0, 0, start()), PEER)
    session.receive(packet(1, 0, media(header())), PEER)
    for seq in range(2, 6):
        now[0] += 3
        session.receive(packet(seq, 0, media(av())), PEER)
    assert session.phase == "active"


def test_partial_control_timeout_clears_both_channels():
    now = [0.0]
    session = receiver(clock=lambda: now[0])
    session.receive(packet(0, 1, control(123)[:20], conv=CONTROL), PEER)
    session.receive(packet(1, 0, b"pending"), PEER)
    now[0] = 2
    with pytest.raises(ReceiveError):
        session.poll()
    assert session.phase == "closed" and session.buffered_bytes == 0


def test_command_tlv_does_not_authorize_session_and_is_reported_after_accept():
    command = b"\x02\x01\x06\x00xx"
    session = receiver()
    with pytest.raises(ReceiveError):
        session.receive(packet(0, 0, command, conv=CONTROL), PEER)
    session = receiver()
    session.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    result = session.receive(packet(1, 0, command, conv=CONTROL), PEER)
    assert result.unhandled_commands == 1 and not result.records
    assert session.phase == "initializing"
    with pytest.raises(ReceiveError):
        session.receive(packet(0, 0, media(header())), PEER)


@pytest.mark.parametrize("conv", [42, 43 | 0x80000000, True, -1])
def test_rejects_unrelated_conversation_pair(conv):
    with pytest.raises(ValueError):
        AvReceiver(PEER, 42, 123, COOKIE, control_conv=conv)
