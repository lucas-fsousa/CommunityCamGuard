"""Shared reader TX contracts only: no network, device, codec or sound output."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_handshake import AvHandshake
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import parse_kcp_segments
from backend.app.drivers.yoosee.p2p.stream_protocol import decrypt_media_tlv
from tests.test_av_handshake import accepted, ack
from tests.test_av_receive import COOKIE, media
from tests.test_captured_media import header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av

FRAME = b"\x3c" + bytes(31)


def active(now, *, repeat_init=False):
    s = AvHandshake(PEER, 42, 123, COOKIE, repeat_init=repeat_init, clock=lambda: now[0])
    s.receive(ack(accepted(s)), PEER)
    s.receive(packet(1, 0, media(header() + av())), PEER)
    assert s.ready
    return s


def sent(s):
    wire, = s.due()
    segment, = parse_kcp_segments(wire)
    s.receive(ack(wire), PEER)
    return wire, segment


def test_message_shares_sequences_and_reader_without_starting_or_closing_video():
    now = [0.0]
    s = active(now)
    talk = s.begin_talk((FRAME,) * 5, wall_us=lambda: 123456789)
    _, encoding = sent(s)
    assert (encoding.conv, encoding.sequence) == (42, 1)
    assert decrypt_media_tlv(encoding.body, COOKIE).hex() == (
        "ffffff8800050000000100020000401f0000a0000110050000000000")
    _, start = sent(s)
    assert (start.conv, start.sequence) == (42 | 0x80000000, 1)
    _, burst = sent(s)
    body = decrypt_media_tlv(burst.body, COOKIE)
    assert (burst.conv, burst.sequence) == (42, 2)
    assert struct.unpack_from('<H', body, 6)[0] == 4
    assert struct.unpack_from('<Q', body, 20)[0] == 123456789
    assert talk.acknowledged_frames == 4
    assert not s.due()  # The next burst cannot race ahead of its 80ms cadence.
    records = s.receive(packet(2, 0, media(av())), PEER).records
    assert records[0].video == b"video" and s.ready
    now[0] = .08
    _, tail = sent(s)
    assert tail.sequence == 3 and talk.acknowledged_frames == 5
    assert not s.due()  # Do not cut off the final 20ms frame.
    now[0] = .101
    _, stop = sent(s)
    assert (stop.conv, stop.sequence) == (42 | 0x80000000, 2)
    assert talk.done and talk.microphone_stopped and s.ready and not s.due()
    s.begin_finish()
    _, close = sent(s)
    assert close.sequence == 4 and struct.unpack_from('<I', close.body, 8)[0] == 7
    assert s.close_acknowledged


def test_cancel_before_transmission_uses_no_sequence_and_does_not_touch_microphone():
    s = active([0.0])
    talk = s.begin_talk((FRAME,))
    talk.cancel()
    assert not s.due() and talk.done and not talk.microphone_started
    s.begin_finish()
    assert sent(s)[1].sequence == 1


@pytest.mark.parametrize("stage", ["header", "start", "audio"])
def test_finish_drains_inflight_push_then_stops_only_the_microphone_before_av_close(stage):
    s = active([0.0])
    talk = s.begin_talk((FRAME,) * 8)
    if stage in ("start", "audio"):
        sent(s)
    if stage == "audio":
        sent(s)
    inflight, = s.due()
    s.begin_finish()
    s.begin_finish()
    assert not s.ready and not s.due()  # Cancellation cannot skip an in-flight sequence.
    s.receive(ack(inflight), PEER)
    if stage == "header":
        assert not s.due() and talk.done and not talk.microphone_started
    else:
        _, stop = sent(s)
        assert stop.conv == 42 | 0x80000000 and talk.microphone_stopped
    _, close = sent(s)
    assert struct.unpack_from('<I', close.body, 8)[0] == 7
    assert s.close_acknowledged


def test_failed_receipt_cannot_leave_a_reusable_route_with_a_sequence_hole():
    now = [0.0]
    s = active(now)
    talk = s.begin_talk((FRAME,))
    sent(s)
    wire, = s.due()  # microphone ON, but no receipt.
    now[0] = .25
    assert s.due() == (wire,)
    now[0] = 2
    with pytest.raises(ReceiveError, match="deadline"):
        s.poll()
    assert s.closed and talk.closed and not talk.microphone_stopped


def test_repeated_talk_and_rejected_input_do_not_reset_or_poison_sequence_owners():
    now = [0.0]
    s = active(now)
    for index in range(2):
        talk = s.begin_talk((FRAME,))
        assert sent(s)[1].sequence == 1 + index * 2
        assert sent(s)[1].sequence == 1 + index * 2
        assert sent(s)[1].sequence == 2 + index * 2
        now[0] += .021
        assert sent(s)[1].sequence == 2 + index * 2
        assert talk.done and s.ready
        with pytest.raises(ValueError):
            s.begin_talk((b"invalid",))
        s.poll()
        assert s.ready


def test_talk_admission_does_not_start_a_new_av_session_or_accept_concurrent_talk():
    s = AvHandshake(PEER, 42, 123, COOKIE)
    with pytest.raises(ReceiveError, match="active"):
        s.begin_talk((FRAME,))
    s = active([0.0])
    s.begin_talk((FRAME,))
    with pytest.raises(ReceiveError, match="active"):
        s.begin_talk((FRAME,))


def test_udp_channel_uses_the_same_selected_transport_and_delivery():
    from backend.app.drivers.yoosee.p2p.account_media_channel import (
        AccountMediaChannel,
        MediaBootstrap,
    )
    from tests.test_account_media_route import SPEC
    from tests.test_cooperative_udp_media import negotiate, session
    from tests.test_hevc_recovery import idr

    owner, job, now, output, video = session()
    negotiate(owner, output)
    channel = AccountMediaChannel(MediaBootstrap(SPEC, video), job, clock=lambda: now[0])
    channel.udp = owner
    talk = channel.begin_talk((FRAME,))
    for _ in range(4):
        owner.step()
        owner.receive(ack(output[-1][0]), owner.peer)
        now[0] += .021
    assert talk.done and talk.microphone_stopped and channel.ready
    assert owner.result is None and video.take().payload == idr() and not video.closed
    assert channel.udp is owner and channel.tcp is None


def test_tcp_candidate_uses_the_existing_relay_wrapper_and_keeps_incoming_video():
    from tests.test_vendor_mtp_tcp_av import canonical, incoming, negotiate, session

    now = [1.0]
    s = session(now)
    negotiate(s)
    talk = s.begin_talk((FRAME,))
    sequences = []
    for _ in range(4):
        wire, = s.due()
        normalized = canonical(wire)
        segment, = parse_kcp_segments(normalized)
        sequences.append((segment.conv, segment.sequence))
        s.receive(incoming(ack(normalized)), PEER)
        now[0] += .021
    assert talk.done and talk.microphone_stopped and s.ready
    assert sequences == [(42, 1), (42 | 0x80000000, 1), (42, 2), (42 | 0x80000000, 2)]
    assert s.receive(incoming(packet(2, 0, media(av()))), PEER).records[0].video == b"video"


def test_microphone_control_starts_after_all_allocated_sdk_init_sequences():
    from tests.test_av_receive_channels import CONTROL, start
    from tests.test_captured_media import control

    now = [0.0]
    s = AvHandshake(PEER, 42, 123, COOKIE, repeat_init=True, clock=lambda: now[0])
    for value in (0, .05, .121, .222):
        now[0] = value
        sent(s)
    s.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    s.receive(packet(0, 0, start()), PEER)
    sent(s)
    s.receive(packet(1, 0, media(header() + av())), PEER)
    s.begin_talk((FRAME,))
    assert sent(s)[1].sequence == 1  # Base channel, after START.
    assert sent(s)[1].sequence == 4  # High channel, after four INITs, not sequence one.


def test_missing_superseded_init_receipt_blocks_talk_not_the_existing_video():
    from tests.test_av_receive_channels import CONTROL, start
    from tests.test_captured_media import control

    now = [0.0]
    s = AvHandshake(PEER, 42, 123, COOKIE, repeat_init=True, clock=lambda: now[0])
    sent(s)
    now[0] = .05
    assert s.due()  # An extra INIT whose receipt never arrives.
    s.receive(packet(0, 0, control(123), conv=CONTROL), PEER)
    s.receive(packet(0, 0, start()), PEER)
    sent(s)
    s.receive(packet(1, 0, media(header() + av())), PEER)
    assert s.ready
    with pytest.raises(ReceiveError, match="INIT sequences"):
        s.begin_talk((FRAME,))
    assert s.ready and not s.due()
    assert s.receive(packet(2, 0, media(av())), PEER).records[0].video == b"video"


@pytest.mark.parametrize('frames', [(), (FRAME,) * 501, (b"invalid",), [FRAME], (bytearray(FRAME),)])
def test_bad_or_unbounded_input_leaves_the_active_video_untouched(frames):
    s = active([0.0])
    with pytest.raises(ValueError):
        s.begin_talk(frames)
    assert s.ready and not s.due()


def test_ten_second_message_is_bounded_and_scheduler_stalls_do_not_create_catchup_bursts():
    now = [0.0]
    s = active(now)
    talk = s.begin_talk((FRAME,) * 500)
    sent(s)
    sent(s)
    for batch in range(125):
        s.receive(packet(2 + batch, 0, media(av())), PEER)
        sent(s)
        assert not s.due()
        now[0] += .081 if batch != 60 else .5
    sent(s)  # Microphone OFF, never AV CLOSE.
    assert talk.done and talk.acknowledged_frames == 500 and s.ready
