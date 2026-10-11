"""Deterministic socket-boundary tests; no networking or real-time sleeps."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.av_sample import AvVideoSample
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import parse_kcp_segments
from backend.app.drivers.yoosee.p2p.mtp_tcp_receive import receive_paired_av
from tests.test_av_handshake import ack
from tests.test_av_receive import media
from tests.test_av_receive_channels import CONTROL, start
from tests.test_captured_media import control, header
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av
from tests.test_vendor_mtp_tcp_av import canonical, incoming, measurement_ack, session


class Socket:
    def __init__(self, now, fault=None):
        self.now, self.fault = now, fault
        self.pending = bytearray()
        self.writes = []
        self.actions = []

    def getpeername(self):
        return (PEER[0], 1) if self.fault == "peer" else PEER

    def settimeout(self, timeout):
        assert 0 < timeout <= 0.2

    def sendall(self, wire):
        self.writes.append(wire)
        if self.fault == "send":
            raise OSError("ambiguous write")
        if wire[:2] == b"\xc0\xe0":
            self.pending.extend(measurement_ack())
        if wire[:2] != b"\xc0\x60":
            return
        normalized = canonical(wire)
        for part in parse_kcp_segments(normalized):
            if len(part.body) != 76:
                continue
            action = struct.unpack_from("<I", part.body, 8)[0]
            self.actions.append(action)
            if action == 7 and self.fault == "close":
                continue
            self.pending.extend(incoming(ack(normalized)))
            if action == 1:
                self.pending.extend(incoming(packet(0, 0, control(123), conv=CONTROL)))
                self.pending.extend(incoming(packet(0, 0, start())))
            if action == 6:
                self.pending.extend(incoming(packet(1, 0, media(header() + av()))))

    def recv(self, size):
        assert size == 4096
        self.now[0] += 0.05
        if self.fault == "eof":
            return b""
        if not self.pending:
            raise TimeoutError
        data = bytes(self.pending[:37])  # Force partial TCP records.
        del self.pending[:37]
        return data


def test_fixed_window_services_broker_and_closes_without_owning_socket():
    now = [1.0]
    owner, sock = session(now), Socket(now)
    ticks = []
    result = receive_paired_av(sock, PEER, owner, clock=lambda: now[0],
                               service_broker=lambda: ticks.append(now[0]))
    assert result.ready and result.close_acknowledged
    assert result.video_frames == 1 and result.audio_frames == 2
    assert result.encoding_headers == 1
    assert sock.actions == [1, 6, 7]
    assert now[0] >= 4 and len(ticks) > 50
    assert owner.closed and owner.buffered_bytes == 0


@pytest.mark.parametrize("fault", ["peer", "send", "eof", "close", "cancel", "broker"])
def test_failures_clear_sample_and_never_recreate_owner(fault):
    now = [1.0]
    owner, sock = session(now), Socket(now, fault)
    sample = AvVideoSample()

    def broker():
        if fault == "broker":
            raise RuntimeError("broker service failed")

    # Stub the codec sink only: these protocol fixtures are not HEVC bitstreams.
    sample.consume = lambda record: None
    with pytest.raises((ReceiveError, OSError, RuntimeError)):
        receive_paired_av(sock, PEER, owner, clock=lambda: now[0], sample=sample,
                          cancelled=lambda: fault == "cancel", service_broker=broker)
    assert owner.closed and owner.buffered_bytes == 0
    assert sample.closed and not sample.data
    if fault in ("cancel", "peer", "broker"):
        assert not sock.writes
    if fault == "send":
        assert len(sock.writes) == 1


@pytest.mark.parametrize("fault", ["elapsed", "cancel"])
def test_broker_service_cannot_extend_deadline_or_hide_cancellation(fault):
    now, cancel = [1.0], [False]
    owner, sock = session(now), Socket(now)

    def broker():
        if fault == "elapsed":
            now[0] += 8
        else:
            cancel[0] = True

    with pytest.raises(ReceiveError):
        receive_paired_av(sock, PEER, owner, clock=lambda: now[0],
                          service_broker=broker, cancelled=lambda: cancel[0])
    assert owner.closed and not sock.writes
