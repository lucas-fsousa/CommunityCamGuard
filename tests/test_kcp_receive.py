from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import KcpReceiver, ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import KCP_ACK, KCP_PUSH, KcpSegment


def segment(seq=0, fragment=0, body=b"data"):
    return KcpSegment(42, KCP_PUSH, fragment, 128, 100, seq, 0, body)


def test_gap_never_acknowledges_missing_data_cumulatively():
    receiver = KcpReceiver(42)
    result = receiver.receive(segment(1, 0, b"tail"))
    assert result.acknowledge and result.unacknowledged == 0 and not result.messages
    result = receiver.receive(segment(0, 1, b"head"))
    assert result.unacknowledged == 2 and result.messages == (b"headtail",)
    assert receiver.buffered_bytes == 0
    assert receiver.receive(segment(1, 0, b"tail")).messages == ()


def test_partial_message_stays_buffered_until_terminal_fragment():
    receiver = KcpReceiver(42)
    assert receiver.receive(segment(0, 2, b"a")).messages == ()
    assert receiver.receive(segment(2, 0, b"c")).unacknowledged == 1
    assert receiver.receive(segment(1, 1, b"b")).messages == (b"abc",)


def test_multiple_messages_released_in_order_after_gap():
    receiver = KcpReceiver(42)
    receiver.receive(segment(2, body=b"c"))
    receiver.receive(segment(1, body=b"b"))
    assert receiver.receive(segment(0, body=b"a")).messages == (b"a", b"b", b"c")


def test_wraparound_and_duplicates():
    receiver = KcpReceiver(42, initial_sequence=0xFFFFFFFF)
    assert receiver.receive(segment(0, body=b"b")).unacknowledged == 0xFFFFFFFF
    result = receiver.receive(segment(0xFFFFFFFF, 1, b"a"))
    assert result.messages == (b"ab",) and result.unacknowledged == 1
    assert receiver.receive(segment(0xFFFFFFFF)).messages == ()


def test_wrong_conversation_ack_and_outside_window_do_not_allocate():
    receiver = KcpReceiver(42)
    for packet in (replace(segment(), conv=43), replace(segment(), command=KCP_ACK), segment(128)):
        assert not receiver.receive(packet).acknowledge
    assert receiver.buffered_bytes == 0


def test_duplicate_does_not_double_buffer_or_extend_deadline():
    clock = [0.0]
    receiver = KcpReceiver(42, clock=lambda: clock[0])
    receiver.receive(segment(1))
    clock[0] = 1.9
    receiver.receive(segment(1))
    assert receiver.buffered_bytes == 4
    clock[0] = 2.0
    with pytest.raises(ReceiveError, match="deadline"):
        receiver.expire()
    assert receiver.closed and receiver.buffered_bytes == 0


@pytest.mark.parametrize("failure", ["bytes", "count", "conflict", "chain", "oversize"])
def test_invalid_data_closes_and_frees_memory(failure):
    receiver = KcpReceiver(42, max_bytes=5, max_segments=3)
    with pytest.raises(ReceiveError):
        if failure == "bytes":
            receiver.receive(segment(0, 1, b"1234"))
            receiver.receive(segment(1, 0, b"56"))
        elif failure == "count":
            receiver.receive(segment(0, 3, b""))
        elif failure == "conflict":
            receiver.receive(segment(1, body=b"a"))
            receiver.receive(segment(1, body=b"b"))
        elif failure == "chain":
            receiver.receive(segment(0, 2, b"a"))
            receiver.receive(segment(1, 0, b"b"))
        else:
            receiver.receive(segment(body=bytes(2018)))
    assert receiver.closed and receiver.buffered_bytes == 0
    with pytest.raises(ReceiveError, match="closed"):
        receiver.receive(segment())


def test_completed_message_resets_assembly_deadline():
    clock = [0.0]
    receiver = KcpReceiver(42, clock=lambda: clock[0])
    receiver.receive(segment())
    clock[0] = 100
    assert receiver.receive(segment(1)).messages == (b"data",)


@pytest.mark.parametrize("kwargs", [dict(max_segments=129), dict(max_bytes=2**30),
                                    dict(timeout=float("nan")), dict(initial_sequence=-1)])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        KcpReceiver(42, **kwargs)
