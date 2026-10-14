import json
import struct
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.motion_collection import MotionCollection
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
from backend.app.drivers.yoosee.p2p.contracts import OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from backend.app.drivers.yoosee.p2p.ptz_authority import PtzGrant
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_read_lifecycle import connection
from tests.test_account_worker_ptz import MotionSocket
from tests.test_broker_reads import NODE
from tests.test_motion_evidence import PROFILE, VALUES
from tests.test_ptz_prepare import ENTRY
from tests.test_ptz_protocol import reply
from tests.test_vendor_p2p_model_session import _response

ENROLLMENT = replace(ENTRY, access_id=123)
TARGET = OnlineDevice(123456, 1, False, 1, bytes(16))


class EvidenceSocket(MotionSocket):
    def __init__(self, fault=None):
        super().__init__()
        self.evidence_fault = fault

    def sendto(self, wire, peer):
        plain = gute_mode2_decrypt(wire, NODE.session_key)
        sequence = struct.unpack_from("<I", plain, 12)[0]
        flags = struct.unpack_from("<I", plain, 20)[0]
        if plain[1] == 0xB7 and not flags & (1 << 20):
            value = VALUES[self.reads % 3]
            self.reads += 1
            if self.evidence_fault == "silent": return len(wire)
            if self.evidence_fault == "axes" and self.reads == 3:
                value = dict(t=1, stVal=dict(ptzInfo=dict(id0_status=0)))
            response = _response(NODE, TARGET, value, sequence=sequence)
            self.queue.append(gute_mode2_encrypt(response, NODE.session_key))
            return len(wire)
        result = super().sendto(wire, peer)
        if plain[1] == 0xB9 and not flags & (1 << 20):
            if json.loads(plain[60:])["data"]["touchType"] == 1:
                self.queue[-1] = finish_mode2(bytearray(reply(source=TARGET.device_id,
                    session=NODE.session_id, sequence=sequence,
                    message=struct.unpack_from("<I", plain, 44)[0],
                    request=struct.unpack_from("<I", plain, 56)[0])), NODE.session_key)
        return result


def test_collect_verify_and_move_on_one_live_generation(monkeypatch):
    sock = EvidenceSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now,
        motion_grants=(PtzGrant(123, TARGET.device_id, PROFILE.directions),))
    collector, motion = [], []
    receive = sock.recvfrom
    def receiving(size):
        if not collector:
            collector.append(MotionCollection(owner, ENROLLMENT, camera_id="cam_test",
                profile=PROFILE, online=TARGET, clock=lambda: sock.now))
        elif not motion and collector[0].poll():
            assert sock.reads == 3 and not sock.moves
            assert not collector[0]._results  # Raw response payloads released after verification.
            motion.append(collector[0].submit("right"))
            with pytest.raises(ValueError): collector[0].submit("right")
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", receiving)
    result = owner.run(lambda _: connection(sock))
    assert result.motion.release_delivery_confirmed
    assert owner.take_motion(motion[0]).release_delivery_confirmed
    assert sock.reads == 3 and len(sock.moves) == 2 and sock.closed == 1
    collector[0].close()


@pytest.mark.parametrize("fault", ["axes", "silent", "stop", "generation"])
def test_incomplete_evidence_or_stopped_account_never_moves(monkeypatch, fault):
    sock = EvidenceSocket(fault)
    owner = AccountReadLifecycle(clock=lambda: sock.now,
        motion_grants=(PtzGrant(123, TARGET.device_id, PROFILE.directions),))
    collector, rejected = [], []
    receive = sock.recvfrom
    def receiving(size):
        if not collector:
            collector.append(MotionCollection(owner, ENROLLMENT, camera_id="cam_test",
                profile=PROFILE, online=TARGET, clock=lambda: sock.now))
        elif not rejected:
            try:
                if collector[0].poll():
                    if fault == "generation":
                        owner._generation += 1  # Simulate replacement after evidence poll.
                    else:
                        owner.stop()
                    collector[0].submit("right")
            except ValueError:
                rejected.append(True)
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", receiving)
    owner.run(lambda _: connection(sock))
    assert rejected and not sock.moves and sock.closed == 1
    collector[0].close()


@pytest.mark.parametrize("online", [replace(TARGET, device_id=654321), replace(TARGET, status=0),
    replace(TARGET, status=True)])
def test_bad_inventory_rejected_before_read_admission(online):
    with pytest.raises(ValueError, match="online"):
        MotionCollection(AccountReadLifecycle(), ENROLLMENT, camera_id="cam_test",
                         profile=PROFILE, online=online)


def test_partial_read_admission_is_cancelled_and_consumed():
    class FullOwner:
        def __init__(self): self.calls, self.cancelled, self.consumed = 0, [], []
        def submit(self, *args):
            self.calls += 1
            if self.calls == 2: raise ValueError("full")
            return type("Ticket", (), {"generation": 1})()
        def cancel(self, ticket): self.cancelled.append(ticket)
        def take_bound(self, ticket): self.consumed.append(ticket)
    owner = FullOwner()
    with pytest.raises(ValueError, match="full"):
        MotionCollection(owner, ENROLLMENT, camera_id="cam_test", profile=PROFILE, online=TARGET)
    assert len(owner.cancelled) == 1 and owner.cancelled == owner.consumed
