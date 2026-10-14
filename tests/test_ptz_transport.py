import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import AccountOutputBudget
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.ptz_transport import run_ptz_transport
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_ptz_protocol import NODE, reply


class Socket:
    def __init__(self, now, failure=None):
        self.now, self.failure = now, failure
        self.sent = []
        self.closed = False
        self.reads = 0

    def settimeout(self, timeout):
        assert timeout == 0.02

    def sendto(self, wire, peer):
        assert not self.closed and peer == NODE.address
        self.sent.append(wire)
        if len(self.sent) == 1:
            if self.failure == "send": raise OSError("ambiguous")
            if self.failure == "interrupt": raise KeyboardInterrupt
            if self.failure == "partial": return 1
        return len(wire)

    def recvfrom(self, size):
        assert size == 4097
        self.reads += 1
        self.now[0] += 0.02
        if self.failure in ("success", "wrong_peer", "plain") and len(self.sent) >= 2:
            frame = gute_mode2_decrypt(self.sent[1], NODE.session_key)
            response = reply(sequence=struct.unpack_from("<I", frame, 16)[0],
                message=struct.unpack_from("<I", frame, 44)[0],
                request=struct.unpack_from("<I", frame, 56)[0])
            wire = response if self.failure == "plain" else finish_mode2(bytearray(response), NODE.session_key)
            peer = ("192.0.2.2", 19800) if self.failure == "wrong_peer" else NODE.address
            return wire, peer
        if self.failure == "receive": raise OSError("gone")
        if self.failure == "flood": return b"noise", NODE.address
        raise TimeoutError

    def close(self):
        self.closed = True
        if self.failure == "close": raise OSError("close failed")


def run(failure=None, cancelled=lambda: False, direction="right"):
    now = [0.0]
    sock = Socket(now, failure)
    def wait(seconds): now[0] += seconds
    args = dict(generation=1, access_id=123, device_id=456, direction=direction,
        reviewed_directions=frozenset({"right"}), cancelled=cancelled,
        clock=lambda: now[0], wait=wait)
    def execute():
        return run_ptz_transport(sock, NODE, BrokerSequences(9, 1, 17),
                                 AccountOutputBudget(), **args)
    return sock, now, execute


@pytest.mark.parametrize("failure", [None, "send", "partial", "receive", "flood", "close"])
def test_uncertain_stop_is_bounded_and_never_replays_start(failure):
    sock, now, execute = run(failure)
    result = execute()
    assert sock.closed and len(sock.sent) == 4
    assert sock.sent[0] != sock.sent[1]
    assert sock.sent[1] == sock.sent[2] == sock.sent[3]
    assert 2 <= now[0] < 2.3
    assert result.motion.reason == "release_unconfirmed"
    assert result.motion.release_attempts == 3
    assert result.received_datagrams <= 65
    assert result.socket_close_failed == (failure == "close")


def test_interrupt_after_start_drains_stop_then_reraises():
    sock, now, execute = run("interrupt")
    with pytest.raises(KeyboardInterrupt): execute()
    assert sock.closed and len(sock.sent) == 4 and 2 <= now[0] < 2.1


def test_prestart_cancel_closes_without_motion():
    sock, _, execute = run(cancelled=lambda: True)
    assert execute().motion.reason == "cancelled"
    assert sock.closed and not sock.sent


def test_invalid_admission_still_closes_owned_socket():
    sock, _, execute = run(direction="up")
    with pytest.raises(ValueError): execute()
    assert sock.closed and not sock.sent


def test_callback_failure_after_start_drains_before_reraising():
    calls = [0]
    def cancelled():
        calls[0] += 1
        if calls[0] > 1: raise RuntimeError("callback failed")
        return False
    sock, now, execute = run(cancelled=cancelled)
    with pytest.raises(RuntimeError): execute()
    assert sock.closed and len(sock.sent) == 4 and now[0] < 2.1


def test_correlated_encrypted_reply_completes_with_one_receipt():
    sock, now, execute = run("success")
    result = execute()
    assert result.motion.release_delivery_confirmed
    assert result.motion.release_attempts == 1
    assert len(sock.sent) == 3 and sock.closed and now[0] < 0.4


@pytest.mark.parametrize("failure", ["wrong_peer", "plain"])
def test_untrusted_reply_never_confirms_stop(failure):
    sock, _, execute = run(failure)
    assert not execute().motion.release_delivery_confirmed
    assert len(sock.sent) == 4 and sock.closed
