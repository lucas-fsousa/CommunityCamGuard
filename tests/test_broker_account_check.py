import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_account_check import run_owned_account_check
from backend.app.drivers.yoosee.p2p.broker_maintenance import BrokerMaintenance
from backend.app.drivers.yoosee.p2p.broker_read_socket import ReadSpec
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from tests.test_broker_heartbeat_reply import reply
from tests.test_broker_reads import DEVICE, NODE, PATH
from tests.test_vendor_p2p_model_session import _response

SPECS = (ReadSpec(DEVICE.device_id, PATH),)


class Socket:
    def __init__(self, fault=None):
        self.now = 0.0
        self.queue = []
        self.closed = self.reads = self.beats = self.acks = 0
        self.timeout = 0.05
        self.fault = fault

    def settimeout(self, timeout): self.timeout = timeout

    def sendto(self, wire, peer):
        if self.fault == "send": raise OSError("private endpoint")
        if self.fault == "interrupt": raise KeyboardInterrupt
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        sequence = struct.unpack_from("<I", plain, 12)[0]
        if struct.unpack_from("<I", plain, 20)[0] & (1 << 20):
            self.acks += 1
            if self.fault == "ack": raise OSError("private ACK failure")
        elif plain[1] == 0xB7:
            self.reads += 1
            if self.fault != "read_silent":
                frame = bytearray(_response(NODE, DEVICE, {}, sequence=sequence))
                flags = struct.unpack_from("<I", frame, 20)[0] | (1 << 18)
                struct.pack_into("<I", frame, 20, flags)
                self.queue.append(gute_mode2_encrypt(bytes(frame), NODE.session_key))
        elif plain[1] == 0xA0:
            self.beats += 1
            if self.fault != "heartbeat_silent":
                self.queue.append(gute_mode2_encrypt(reply(sequence=sequence, flags=(2 << 16) | (1 << 21) | (1 << 18)), NODE.session_key))
        else:
            pytest.fail("unexpected command")
        return len(wire)

    def recvfrom(self, size):
        assert size == 4096
        if self.fault == "receive": raise OSError("private receive failure")
        if self.queue:
            return self.queue.pop(0), NODE.address
        self.now += self.timeout
        raise TimeoutError

    def close(self):
        self.closed += 1
        if self.fault == "close": raise OSError("private close failure")


def run(sock, specs=SPECS, **kwargs):
    return run_owned_account_check(sock, NODE, 1, specs, "192.0.2.1", 1234,
                                    clock=lambda: sock.now, **kwargs)


def test_reads_on_both_sides_of_heartbeat_share_one_socket_and_sequence():
    sock = Socket()
    result = run(sock)
    assert result.failure is None and sock.closed == 1
    assert [r.request.ticket.sequence for r in result.before + result.after] == [17, 19]
    assert result.heartbeats[0].ticket.sequence == 18
    assert all(r.status == "response" for r in result.before + result.after)
    assert sock.reads == 2 and sock.beats == 1 and sock.now >= 50
    assert result.sent_datagrams == 6 and result.received_datagrams == 3


@pytest.mark.parametrize("fault,failure", [("send", "socket"), ("receive", "socket"),
    ("ack", "socket"), ("read_silent", "read_incomplete"),
    ("heartbeat_silent", "heartbeat_timeout"), ("close", "socket_close")])
def test_failures_close_without_retry_or_hiding_completed_reads(fault, failure):
    sock = Socket(fault)
    result = run(sock)
    assert result.failure == failure and sock.closed == 1
    assert sock.beats <= 1 and sock.reads <= 2
    assert "private" not in repr(result)
    if fault == "ack": assert result.before[0].status == "response"


def test_cancel_and_invalid_batch_close_before_sending():
    for specs, cancelled in [((), lambda: False), (SPECS, lambda: True)]:
        sock = Socket()
        result = run(sock, specs, cancelled=cancelled)
        assert result.failure is not None and sock.closed == 1
        assert not sock.reads and not sock.beats


def test_base_exception_still_closes_socket():
    sock = Socket("interrupt")
    with pytest.raises(KeyboardInterrupt): run(sock)
    assert sock.closed == 1


def test_account_profile_does_not_extend_default_media_maintenance():
    now = [0.0]
    default = BrokerMaintenance(NODE, clock=lambda: now[0])
    account = BrokerMaintenance.account_check(NODE, clock=lambda: now[0])
    now[0] = 16
    with pytest.raises(ValueError): default.receive(b"", NODE.address)
    assert account.receive(b"", NODE.address) == ()
    now[0] = 60
    with pytest.raises(ValueError): account.receive(b"", NODE.address)
