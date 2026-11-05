import struct

import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import build_mtp_frame
from backend.app.drivers.yoosee.p2p.mtp_tcp_heartbeat import TcpMeterHeartbeat
from tests.test_continuous_media import session
from tests.test_media_receive import PEER
from tests.test_vendor_mtp_tcp_av import measurement_ack, negotiate


def heartbeat(now):
    return TcpMeterHeartbeat(link_id=42, source_id=17, destination_id=23,
                             call_id=123, clock=lambda: now[0])


def receipt(wire):
    sequence, tick = struct.unpack_from('<IQ', wire, 14 + 28)
    return measurement_ack(sequence=sequence, timestamp=tick)


def test_periodic_probe_has_new_identity_and_only_one_pending_receipt(caplog):
    now = [1.0]
    owner = heartbeat(now)
    assert owner.due() is None
    now[0] = 2
    wire = owner.due()
    assert wire[:2] == b'\xc0\xe0'
    assert struct.unpack_from('<IQ', wire, 42) == (2, 2000)
    assert owner.due() is None
    assert owner.observe(receipt(wire)) and owner.confirmed == 1
    assert not owner.observe(receipt(wire))
    assert 'periodic meter acknowledged sequence=2' in caplog.text
    now[0] = 3
    other = owner.due()
    assert struct.unpack_from('<IQ', other, 42) == (3, 3000)
    assert not owner.observe(receipt(wire)) and owner.confirmed == 1


@pytest.mark.parametrize('fault', ['source', 'destination', 'link', 'sequence', 'timestamp',
    'flags', 'checksum', 'prefix', 'truncated'])
def test_unrelated_or_damaged_receipt_cannot_confirm_or_extend_the_probe(fault):
    now = [0.0]
    owner = heartbeat(now)
    now[0] = 1
    wire = owner.due()
    reply = receipt(wire)
    body = bytearray(reply[14:])
    if fault == 'source': struct.pack_into('<Q', body, 12, 24)
    elif fault == 'destination': struct.pack_into('<Q', body, 20, 18)
    elif fault == 'link': struct.pack_into('<I', body, 4, 43)
    elif fault == 'sequence': struct.pack_into('<I', body, 28, 1)
    elif fault == 'timestamp': struct.pack_into('<Q', body, 32, 999)
    elif fault == 'flags': struct.pack_into('<I', body, 8, 8)
    reply = build_mtp_frame(0x90 if fault == 'prefix' else 0xD0, b'opaque!!' + body)
    if fault == 'checksum': reply = reply[:4] + bytes((reply[4] ^ 1,)) + reply[5:]
    elif fault == 'truncated': reply = reply[:-1]
    now[0] = 3.9
    assert not owner.observe(reply) and owner.confirmed == 0
    assert owner.due() is None
    now[0] = 4
    next_wire = owner.due()
    assert struct.unpack_from('<IQ', next_wire, 42) == (3, 4000)
    assert next_wire != wire and not owner.observe(receipt(wire))


def test_large_time_jump_never_creates_a_catch_up_burst_and_close_cancels_pending():
    now = [0.0]
    owner = heartbeat(now)
    now[0] = 10000
    wire = owner.due()
    assert wire and owner.due() is None
    owner.close()
    assert owner._pending is None and not owner.observe(receipt(wire))
    with pytest.raises(ValueError, match='closed'): owner.due()


@pytest.mark.parametrize('instant', [-1.0, float('nan'), float('inf')])
def test_bad_maintenance_clock_is_not_used_to_renew_a_session(instant):
    now = [1.0]
    owner = heartbeat(now)
    now[0] = instant
    with pytest.raises(ValueError, match='clock'): owner.due()


def test_confirmed_periodic_probes_do_not_keep_a_silent_media_stream_alive():
    now = [1.0]
    owner = session(now)
    negotiate(owner)
    receiver = owner._av
    for instant in (2, 3, 4, 5):
        now[0] = instant
        wire, = owner.due()
        assert wire[:2] == b'\xc0\xe0'
        assert not owner.receive(receipt(wire), PEER).records
        assert owner.ready and owner._av is receiver
    assert owner._heartbeat.confirmed == 4
    now[0] = 6
    with pytest.raises(ReceiveError): owner.poll()
    assert owner.closed


def test_periodic_maintenance_stops_before_avclose_without_another_socket():
    now = [1.0]
    owner = session(now)
    negotiate(owner)
    now[0] = 2
    assert owner.due()[0][:2] == b'\xc0\xe0'
    owner.begin_finish()
    assert owner._heartbeat.closed
    closing, = owner.due()
    assert closing[:2] == b'\xc0\x60'
    now[0] = 2.5
    assert owner.due() == (closing,)
    owner.close()
