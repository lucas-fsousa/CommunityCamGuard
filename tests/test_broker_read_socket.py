import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_read_socket import ReadSpec, run_owned_reads
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from backend.app.drivers.yoosee.p2p.wire import new_header
from tests.test_broker_model_reply import DEVICE, NODE
from tests.test_broker_reads import PATH
from tests.test_broker_transactions import ack


class Socket:
    def __init__(self, now, fault=None):
        self.now, self.fault = now, fault
        self.writes, self.pending = [], []
        self.closed = False

    def settimeout(self, value):
        assert 0 < value <= 0.2

    def sendto(self, wire, peer):
        assert peer == NODE.address
        self.writes.append(wire)
        if self.fault == "send_timeout": raise TimeoutError("send timeout")
        if self.fault == "send": raise OSError("private endpoint")
        if self.fault == "partial": return len(wire) - 1
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        if plain[1] != 0xB7:
            if self.fault == "ack_send": raise OSError("ACK write")
            return len(wire)
        seq = struct.unpack_from("<I", plain, 12)[0]
        payload = json.dumps({"seq": seq}).encode()
        reply = new_header(0xB8, 40 + len(payload), NODE.session_id, 999,
                            (2 << 16) | (1 << 21) | (1 << 18))
        struct.pack_into("<I", reply, 16, seq)
        struct.pack_into("<Q", reply, 24, DEVICE.device_id)
        reply[32] = 1
        struct.pack_into("<H", reply, 38, len(payload))
        reply[40:] = payload
        self.pending.insert(0, gute_mode2_encrypt(bytes(reply), NODE.session_key))
        return len(wire)

    def recvfrom(self, size):
        assert size == 4096
        self.now[0] += 0.05
        if self.fault == "receive": raise OSError("read failed")
        if self.fault == "malformed": return b"\x70\x01", NODE.address
        if self.fault == "deadline": self.now[0] += 15
        if self.fault == "ack_only":
            return gute_mode2_encrypt(ack(sequence=NODE.next_sequence, kind=0xB7,
                session=NODE.session_id), NODE.session_key), NODE.address
        if self.fault == "silent" or not self.pending: raise TimeoutError
        return self.pending.pop(0), NODE.address

    def close(self):
        self.closed = True
        if self.fault == "close": raise OSError("close failed")


def test_interleaved_reads_send_once_and_always_close_owned_socket():
    now = [0.0]
    sock = Socket(now)
    specs = (ReadSpec(DEVICE.device_id, PATH),) * 3
    result = run_owned_reads(sock, NODE, 1, specs, clock=lambda: now[0])
    assert result.failure is None and sock.closed
    assert len(result.completions) == 3
    assert all(r.status == "response" for r in result.completions)
    assert [r.request.ticket.sequence for r in result.completions] == [19, 18, 17]
    assert result.sent_datagrams == 6 and result.received_datagrams == 3


@pytest.mark.parametrize("fault,expected", [("send", "socket"), ("partial", "socket"),
    ("send_timeout", "socket"),
    ("receive", "socket"), ("malformed", "protocol"), ("deadline", "deadline"),
    ("close", "socket_close"), ("ack_send", "socket")])
def test_failure_is_sanitized_no_retry_and_completions_not_lost(fault, expected):
    now = [0.0]
    sock = Socket(now, fault)
    result = run_owned_reads(sock, NODE, 1, (ReadSpec(DEVICE.device_id, PATH),), clock=lambda: now[0])
    assert sock.closed and result.failure == expected and len(result.completions) == 1
    assert "private" not in repr(result)
    if fault in {"ack_send", "close"}:
        assert result.completions[0].status == "response"
    else:
        assert result.completions[0].status == "cancelled"
    if fault in {"send", "partial"}:
        assert len(sock.writes) == 1


@pytest.mark.parametrize("fault", ["silent", "ack_only"])
def test_silence_and_ack_duplicates_expire_without_new_request(fault):
    now = [0.0]
    sock = Socket(now, fault)
    result = run_owned_reads(sock, NODE, 1, (ReadSpec(DEVICE.device_id, PATH, 0.2),), clock=lambda: now[0])
    assert result.failure is None and result.completions[0].status == "timeout"
    assert len(sock.writes) == 1 and sock.closed


@pytest.mark.parametrize("invalid", [False, True])
def test_cancel_before_write_and_invalid_batch_close_socket(invalid):
    now = [0.0]
    sock = Socket(now)
    specs = (ReadSpec(DEVICE.device_id, PATH), ReadSpec(DEVICE.device_id, "not_allowed"))
    result = run_owned_reads(sock, NODE, 1, specs if invalid else specs[:1],
                             clock=lambda: now[0], cancelled=lambda: True)
    assert result.failure == ("invalid" if invalid else "cancelled")
    assert not sock.writes and sock.closed
    assert all(r.status == "cancelled" for r in result.completions)


def test_expired_unsent_requests_are_never_written():
    now = [0.0]
    class SlowSocket(Socket):
        def sendto(self, wire, peer):
            size = super().sendto(wire, peer)
            now[0] += 0.3
            return size
    sock = SlowSocket(now)
    result = run_owned_reads(sock, NODE, 1, (ReadSpec(DEVICE.device_id, PATH, 0.2),) * 2,
                             clock=lambda: now[0])
    assert result.failure is None and len(sock.writes) == 1
    assert len(result.completions) == 2
    assert all(r.status == "timeout" for r in result.completions)
    assert sock.closed


def test_cancellation_after_read_does_not_accept_late_response():
    now = [0.0]
    sock = Socket(now)
    result = run_owned_reads(sock, NODE, 1, (ReadSpec(DEVICE.device_id, PATH),),
                             clock=lambda: now[0], cancelled=lambda: now[0] > 0)
    assert result.failure == "cancelled" and result.received_datagrams == 1
    assert result.completions[0].status == "cancelled" and sock.closed


@pytest.mark.parametrize("stage", ["clock", "cancel"])
def test_base_exception_still_closes_transferred_socket(stage):
    now = [0.0]
    sock = Socket(now)
    def abort():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run_owned_reads(sock, NODE, 1, (ReadSpec(DEVICE.device_id, PATH),),
                         clock=abort if stage == "clock" else lambda: now[0],
                         cancelled=abort if stage == "cancel" else lambda: False)
    assert sock.closed and not sock.writes


@pytest.mark.parametrize("count", [0, 9])
def test_batch_bounds_checked_before_network_write(count):
    now = [0.0]
    sock = Socket(now)
    result = run_owned_reads(sock, NODE, 1, (ReadSpec(DEVICE.device_id, PATH),) * count,
                             clock=lambda: now[0])
    assert result.failure == "invalid" and not sock.writes and sock.closed
