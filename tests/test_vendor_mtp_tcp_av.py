import struct

import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import build_mtp_frame
from backend.app.drivers.yoosee.p2p.mtp_tcp_av import TcpAvSession
from backend.app.drivers.yoosee.p2p.mtp_tcp_handshake import build_mtp_tcp_pair_request
from tests.test_av_handshake import ack
from tests.test_av_receive import COOKIE, media
from tests.test_av_receive_channels import CONTROL, start
from tests.test_captured_media import control, header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av


def session(now=None):
    now = now if now is not None else [1.0]
    return TcpAvSession(PEER, 42, 123, COOKIE, source_id=17, destination_id=23,
                        clock=lambda: now[0])


def incoming(canonical):
    return build_mtp_frame(0x50, b"opaque!!" + canonical[6:])


def canonical(outgoing):
    assert outgoing[:2] == b"\xc0\x60"
    return build_mtp_frame(0x10, outgoing[14:])


def measurement_ack(sequence=1, timestamp=1000, source=23):
    body = bytearray(72)
    body[:4] = b"\x00\x02\x44\x00"
    struct.pack_into("<I", body, 4, 42)
    struct.pack_into("<QQIQ", body, 12, source, 17, sequence, timestamp)
    struct.pack_into("<I", body, 52, 72)
    return build_mtp_frame(0xD0, b"opaque!!" + body)


def negotiate(s):
    measurement, = s.due()
    assert measurement[:2] == b"\xc0\xe0"
    assert not s.due() and not s.metered
    assert not s.receive(measurement_ack(), PEER).records
    assert s.metered and not s.ready
    init, = s.due()
    s.receive(incoming(ack(canonical(init)))
              + incoming(packet(0, 0, control(123), conv=CONTROL))
              + incoming(packet(0, 0, start())), PEER)
    local_start, = s.due()
    assert not s.ready
    batch = s.receive(incoming(ack(canonical(local_start)))
                      + incoming(packet(1, 0, media(header() + av()))), PEER)
    assert s.ready and any(record.video for record in batch.records)
    assert all(wire[:2] == b"\xc0\x60" for wire in batch.acknowledgements)


def test_complete_lifecycle_with_close_receipt_and_no_reuse():
    s = session()
    negotiate(s)
    s.begin_finish()
    s.begin_finish()
    assert not s.ready
    close, = s.due()
    s.receive(incoming(ack(canonical(close))), PEER)
    assert s.close_acknowledged
    s.finish()
    assert s.closed and not s.close_acknowledged and not s.metered
    assert s.buffered_bytes == 0
    with pytest.raises(ReceiveError):
        s.due()


@pytest.mark.parametrize("kwargs", [{"sequence": 2}, {"timestamp": 999}, {"source": 24}])
def test_wrong_measurement_cannot_start_av_or_extend_deadline(kwargs):
    now = [1.0]
    s = session(now)
    s.due()
    s.receive(measurement_ack(**kwargs), PEER)
    assert not s.metered and not s.due()
    now[0] = 4.0
    with pytest.raises(ReceiveError):
        s.poll()
    assert s.closed


def test_unsent_measurement_ack_and_duplicate_do_not_reset_av():
    s = session()
    s.receive(measurement_ack(), PEER)
    assert not s.metered
    s.due()
    wire = measurement_ack()
    for byte in wire:
        s.receive(bytes((byte,)), PEER)
    init, = s.due()
    s.receive(wire, PEER)
    assert not s.due()  # No newly created INIT sender/sequence.
    assert canonical(init)


@pytest.mark.parametrize("fault", ["peer", "early_kcp", "wrong_conv", "checksum", "type"])
def test_terminal_input_failures_release_buffers(fault):
    s = session()
    s.due()
    if fault != "early_kcp":
        s.receive(measurement_ack(), PEER)
        s.due()
    wire = incoming(packet(0, 0, start(), conv=43 if fault == "wrong_conv" else 42))
    peer = (PEER[0], PEER[1] + 1) if fault == "peer" else PEER
    if fault == "checksum":
        wire = wire[:4] + bytes((wire[4] ^ 1,)) + wire[5:]
    if fault == "type":
        wire = bytearray(wire)
    with pytest.raises(ReceiveError):
        s.receive(wire, peer)
    assert s.closed and s.buffered_bytes == 0


def test_read_budget_does_not_reset_between_calls(monkeypatch):
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.mtp_tcp_av.MAX_RX_BYTES", 100)
    s = session()
    s.due()
    s.receive(measurement_ack(), PEER)
    with pytest.raises(ReceiveError):
        s.receive(measurement_ack(), PEER)
    assert s.closed


def test_transmit_budget_counts_first_measurement(monkeypatch):
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.mtp_tcp_av.MAX_TX_BYTES", 85)
    s = session()
    with pytest.raises(ReceiveError):
        s.due()
    assert s.closed


def test_absolute_lifetime_also_applies_to_active_media():
    now = [1.0]
    s = session(now)
    negotiate(s)
    now[0] = 16.0
    with pytest.raises(ReceiveError):
        s.poll()
    assert s.closed


def test_eof_with_partial_record_always_closes_and_premature_close_fails():
    s = session()
    s.receive(measurement_ack()[:10], PEER)
    with pytest.raises(ValueError):
        s.finish()
    assert s.closed and not s.buffered_bytes
    s = session()
    with pytest.raises(ReceiveError):
        s.begin_finish()
    assert s.closed


@pytest.mark.parametrize("extended", [True, False])
def test_correlated_maintenance_requests_use_same_owner_and_do_not_start_av(extended):
    s = session()
    body = build_mtp_tcp_pair_request(relay_link_id=42, source_id=23,
                                      destination_id=17, timestamp_ms=123)[6:]
    wire = build_mtp_frame(0xD0 if extended else 0x90,
                           (b"opaque!!" if extended else b"") + body)
    reply, = s.receive(wire, PEER).acknowledgements
    assert reply[:2] == (b"\xc0\xe0" if extended else b"\xc0\x80")
    assert not s.metered and not s.ready
    s.close()
    assert not s.buffered_bytes


def test_partial_send_cancellation_cannot_recreate_measurement_or_init():
    s = session()
    s.due()
    s.close()  # Caller observed an ambiguous sendall failure.
    s.close()
    with pytest.raises(ReceiveError):
        s.receive(measurement_ack(), PEER)
    with pytest.raises(ReceiveError):
        s.due()


def test_record_budget_includes_ignored_duplicate_measurement_acks(monkeypatch):
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.mtp_tcp_av.MAX_RECORDS", 1)
    s = session()
    s.due()
    s.receive(measurement_ack(), PEER)
    with pytest.raises(ReceiveError):
        s.receive(measurement_ack(), PEER)
    assert s.closed and s.buffered_bytes == 0
