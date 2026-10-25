"""Read-only B9 contract, strict response correlation and shared sequence ownership."""

import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadJob, LampReadSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from tests.test_broker_transactions import ack

NODE = CertifiedNode(("192.0.2.10", 19800), 17, bytes(range(32)), 40)
SPEC = LampReadSpec(123, 7000000002)


def fixture():
    now = [0.0]
    source = BrokerSequences(NODE.session_id, 1, 40)
    job = LampReadJob(NODE, source, SPEC, generation=1, deadline=2, clock=lambda: now[0])
    return job, source, now


def response(job, *, state=0, kind=12, session=17, access=123, device=7000000002, request=None, direction=0, node=NODE):
    payload = bytes((1, direction, 0, 0)) + struct.pack("<I", job._request_id if request is None else request)
    payload += json.dumps({"type":kind,"data":{"whiteLightStatus":state}}, separators=(",", ":")).encode()
    frame = new_header(0xB9, 0x34+len(payload), session, 200, (2 << 16) | (1 << 18))
    struct.pack_into("<Q", frame, 28, access)
    struct.pack_into("<Q", frame, 36, device)
    struct.pack_into("<I", frame, 44, 77)
    struct.pack_into("<H", frame, 48, len(payload))
    frame[52:] = payload
    return gute_mode2_decrypt(finish_mode2(frame, node.session_key), node.session_key)


def test_request_is_exactly_read_type12_and_ack_is_not_semantic_completion():
    job, source, _now = fixture()
    plain = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    assert json.loads(plain[60:]) == {"type":12}
    assert job.next_packet() is None
    job.observe(ack(40, session=17), NODE.address, generation=1)
    assert job.result is None
    packets = job.observe(response(job), NODE.address, generation=1)
    assert len(packets) == 2
    receipt = gute_mode2_decrypt(packets[-1], NODE.session_key)
    assert receipt[1] == 0xBA and struct.unpack_from("<I", receipt, 12)[0] == 41
    assert job.result.status == "response" and job.result.enabled is False
    assert job.result.transport_acknowledged
    assert source.allocate() == 42 and source.matches(17, 1)


@pytest.mark.parametrize("change", [{"session":18}, {"access":124}, {"device":7000000003},
                                    {"request":99}, {"kind":11}, {"direction":1}])
def test_unrelated_or_write_responses_cannot_complete_read(change):
    job, _source, _now = fixture()
    job.next_packet()
    assert not job.observe(response(job, **change), NODE.address, generation=1)
    assert job.result is None
    assert job.observe(response(job, state=1), NODE.address, generation=1)
    assert job.result.enabled is True


def test_wrong_peer_generation_deadline_and_cancellation_keep_namespace_owned():
    job, source, now = fixture()
    job.next_packet()
    plain = response(job)
    assert not job.observe(plain, ("192.0.2.99", 19800), generation=1)
    assert not job.observe(plain, NODE.address, generation=2)
    now[0] = 2
    assert not job.observe(plain, NODE.address, generation=1)
    assert job.next_packet() is None and job.result.status == "timeout"
    assert source.matches(17, 1)
    other, other_source, _ = fixture()
    other.cancel()
    assert other.next_packet() is None and other.result.status == "cancelled"
    assert other_source.matches(17, 1)


def test_rejected_transport_or_nonbinary_state_is_not_success():
    job, _source, _ = fixture()
    job.next_packet()
    job.observe(ack(40, session=17, result=7), NODE.address, generation=1)
    assert job.result.status == "transport_rejected" and job.result.enabled is None
    other, _source, _ = fixture()
    other.next_packet()
    assert other.observe(response(other, state=2), NODE.address, generation=1)
    assert other.result.status == "invalid_response" and other.result.enabled is None


@pytest.mark.parametrize("deadline", [True, 0, -1, 3, float("nan"), float("inf")])
def test_invalid_deadline_consumes_no_shared_sequence(deadline):
    source = BrokerSequences(17, 1, 40)
    with pytest.raises(ValueError):
        LampReadJob(NODE, source, SPEC, generation=1, deadline=deadline, clock=lambda: 0)
    assert source.allocate() == 40


def test_retired_namespace_can_never_emit_or_accept_another_request():
    job, source, _ = fixture()
    source.close()
    assert job.next_packet() is None and job.result.status == "cancelled"


def test_peer_ack_cannot_extend_the_original_deadline():
    job, _source, now = fixture()
    job.next_packet()
    now[0] = 1.9
    job.observe(ack(40, session=17), NODE.address, generation=1)
    now[0] = 2.0
    assert not job.observe(response(job), NODE.address, generation=1)
    job.next_packet()
    assert job.result.status == "timeout" and job.result.enabled is None
