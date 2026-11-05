import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import KcpAssemblyTimeout, KcpReceiver
from backend.app.drivers.yoosee.p2p.kcp_window import build_window_report
from backend.app.drivers.yoosee.p2p.media_protocol import (
    KCP_HEADER,
    KCP_WASK,
    KCP_WINS,
    build_kcp_push,
    build_mtp_frame,
    parse_kcp_segments,
)
from backend.app.drivers.yoosee.p2p.media_receive import MediaReceiver
from backend.app.drivers.yoosee.p2p.mtp_tcp_kcp import unwrap_tcp_relay_kcp, wrap_tcp_relay_kcp
from tests.test_media_receive import PEER, packet


def probe(*, conv=42, command=KCP_WASK, fragment=0, body=b''):
    return build_mtp_frame(0x10, KCP_HEADER.pack(
        conv, command, fragment, 128, 123, 999, 999, len(body)) + body)


def test_window_probe_reports_current_credit_after_a_gap_is_filled():
    receiver = KcpReceiver(42, max_segments=4, clock=lambda: 0)
    media = MediaReceiver(PEER, receiver)
    media.receive(packet(0, 1, b'head'), PEER)
    for sequence in (2, 3):
        media.receive(packet(sequence, 0, b'next'), PEER)
    assert receiver.available_window == 1
    batch = media.receive(probe(), PEER)
    report, = parse_kcp_segments(batch.acknowledgements[0])
    assert (report.command, report.window, report.unacknowledged) == (KCP_WINS, 1, 1)
    assert not batch.messages
    batch = media.receive(packet(1, 0, b'tail'), PEER)
    assert batch.messages == (b'headtail', b'next', b'next')
    # Pretend this PUSH ACK never reached the peer; only a fresh WASK arrives.
    batch = media.receive(probe(), PEER)
    report, = parse_kcp_segments(batch.acknowledgements[0])
    assert (report.command, report.window, report.unacknowledged) == (KCP_WINS, 4, 4)
    assert receiver.buffered_bytes == 0 and not batch.messages


def test_future_segment_cannot_occupy_the_missing_heads_reserved_slot():
    receiver = KcpReceiver(42, max_segments=4, clock=lambda: 0)
    media = MediaReceiver(PEER, receiver)
    media.receive(packet(0, 1, b'head'), PEER)
    for sequence in (2, 3):
        assert media.receive(packet(sequence, 0, b'future'), PEER).acknowledgements
    held_bytes = receiver.buffered_bytes
    assert not media.receive(packet(4, 0, b'retransmit'), PEER).acknowledgements
    assert receiver.buffered_bytes == held_bytes and receiver.available_window == 1
    assert media.receive(packet(1, 0, b'tail'), PEER).messages == (b'headtail', b'future', b'future')
    assert media.receive(packet(4, 0, b'retransmit'), PEER).messages == (b'retransmit',)
    assert receiver.buffered_bytes == 0 and not receiver.closed


@pytest.mark.parametrize('case', ['peer', 'conv', 'checksum', 'body', 'fragment', 'wins', 'unknown'])
def test_invalid_or_unrelated_probe_cannot_get_a_report_or_change_receive_state(case):
    receiver = KcpReceiver(42)
    media = MediaReceiver(PEER, receiver)
    peer = PEER
    wire = probe()
    if case == 'peer': peer = (PEER[0], PEER[1] + 1)
    elif case == 'conv': wire = probe(conv=43)
    elif case == 'body': wire = probe(body=b'not-a-probe')
    elif case == 'fragment': wire = probe(fragment=1)
    elif case == 'wins': wire = probe(command=KCP_WINS)
    elif case == 'unknown': wire = probe(command=0x55)
    else:
        damaged = bytearray(wire); damaged[4] ^= 1; wire = bytes(damaged)
    batch = media.receive(wire, peer)
    assert not batch.acknowledgements and not batch.messages
    assert receiver.next_sequence == 0 and receiver.buffered_bytes == 0


def test_probe_is_not_message_progress_and_does_not_keep_missing_fragments_alive():
    now = [0.0]
    receiver = KcpReceiver(42, clock=lambda: now[0])
    media = MediaReceiver(PEER, receiver)
    media.receive(packet(1, 0, b'future'), PEER)
    now[0] = 1.9
    assert media.receive(probe(), PEER).acknowledgements
    now[0] = 2.0
    with pytest.raises(KcpAssemblyTimeout): media.receive(probe(), PEER)
    assert receiver.closed


@pytest.mark.parametrize('window', [0, 1, 128])
def test_window_report_uses_existing_authenticated_tcp_route_wrapping(window):
    canonical = build_window_report(42, unacknowledged=100, window=window)
    outgoing = wrap_tcp_relay_kcp(canonical, destination_id=1234, expected_conversation=42)
    # Simulate the relay's mapped inbound envelope; never accept outbound as inbound.
    inbound = build_mtp_frame(0x50, outgoing[6:])
    normalized = unwrap_tcp_relay_kcp(inbound, expected_conversation=42)
    report, = parse_kcp_segments(normalized)
    assert (report.conv, report.command, report.window, report.unacknowledged) == (42, KCP_WINS, window, 100)
    assert report.body == b''


@pytest.mark.parametrize('field,value', [('conv', -1), ('conv', True), ('unacknowledged', 2**32),
    ('window', -1), ('window', 65536), ('window', True)])
def test_report_rejects_unbounded_or_noninteger_fields(field, value):
    fields = dict(conv=42, unacknowledged=0, window=128)
    fields[field] = value
    with pytest.raises(ValueError): build_window_report(**fields)


@pytest.mark.parametrize('window', [-1, 65536, True])
def test_push_cannot_advertise_an_invalid_receive_window(window):
    with pytest.raises(ValueError): build_kcp_push(42, 0, b'data', window=window)
