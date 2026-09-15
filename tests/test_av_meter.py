import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_meter import AvMeter
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import (
    build_media_meter_request,
    build_mtp_frame,
    parse_media_meter,
)
from tests.test_av_probe import FakeSocket, run
from tests.test_media_receive import PEER


def request():
    return build_media_meter_request(22, 11, 42, 123, sequence=9, timestamp=8)


def responder():
    return AvMeter(PEER, 42, 123, 11, 22)


def test_matching_request_response_echoes_only_transport_identity():
    meter = responder()
    result = meter.receive(request(), PEER)
    ack = parse_media_meter(result)
    assert (ack.kind, ack.source_id, ack.destination_id, ack.link_id, ack.sequence, ack.timestamp) == (2, 11, 22, 42, 9, 8)
    assert meter.acknowledgements == 1
    assert meter.receive(result, PEER) is None


@pytest.mark.parametrize("field,offset,value", [
    ("link", 4, 43), ("source", 12, 23), ("destination", 20, 12),
    ("call", 68, 124), ("channel", 48, 3), ("length", 52, 0),
])
def test_mismatched_requests_are_not_acknowledged(field, offset, value):
    inner = bytearray(request()[6:])
    struct.pack_into("<I", inner, offset, value)
    meter = responder()
    assert meter.receive(build_mtp_frame(0x90, inner), PEER) is None
    assert meter.acknowledgements == 0


def test_observed_short_meter_without_call_uses_remaining_route_identity():
    inner = bytearray(request()[6:-4])
    struct.pack_into("<I", inner, 52, 68)
    assert responder().receive(build_mtp_frame(0x90, inner), PEER) is not None


def test_bad_checksum_and_wrong_peer_never_receive_response():
    wire = request()
    assert responder().receive(wire, (PEER[0], 1)) is None
    assert responder().receive(wire[:-1] + bytes([wire[-1] ^ 1]), PEER) is None


def test_duplicate_requests_may_retry_ack_but_cannot_exhaust_resources():
    meter = responder()
    for _ in range(64):
        assert meter.receive(request(), PEER)
    with pytest.raises(ReceiveError, match="budget"):
        meter.receive(request(), PEER)


def test_socket_probe_sends_meter_response_without_treating_it_as_av_readiness():
    class MeterSocket(FakeSocket):
        def sendto(self, wire, peer):
            if wire[:2] == b"\xc0\x90":
                self.sent.append(wire)
                return len(wire)
            return super().sendto(wire, peer)
    sock = MeterSocket()
    sock.incoming.append((request(), PEER))
    result = run(sock, duration=0.5, meter=responder())
    assert result.meter_acknowledgements == 1 and result.ready and sock.closed
