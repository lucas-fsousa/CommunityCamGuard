"""Synthetic encrypted lamp write replies; no camera/network/light changes."""

import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.lamp_control_specs import LampReadSpec, LampWriteSpec
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadJob
from backend.app.drivers.yoosee.p2p.lamp_write_job import LampWriteJob
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_broker_transactions import ack
from tests.test_lamp_read_job import NODE, response


def fixture(enabled=True):
    now = [0.0]
    source = BrokerSequences(NODE.session_id, 1, 40)
    job = LampWriteJob(NODE, source, LampWriteSpec(123, 7000000002, enabled),
                       generation=1, deadline=2, clock=lambda: now[0])
    return job, source, now


def reply(job, *, error=0, omit_error=False, node=NODE, **kwargs):
    plain = bytearray(response(job, kind=11, node=node, **kwargs))
    value = {'type': 11}
    if not omit_error:
        value['err'] = error
    payload = plain[52:60] + json.dumps(value, separators=(',', ':')).encode()
    plain = plain[:52] + payload
    struct.pack_into('<H', plain, 2, len(plain))
    struct.pack_into('<H', plain, 48, len(payload))
    return gute_mode2_decrypt(finish_mode2(plain, node.session_key), node.session_key)


@pytest.mark.parametrize('enabled', [False, True])
def test_exact_type11_boolean_and_correlated_application_reply_not_transport_ack(enabled):
    job, source, _ = fixture(enabled)
    plain = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    assert json.loads(plain[60:]) == {'type': 11, 'data': {'whiteLightCtrl': int(enabled), 'whiteLightStatus': 0}}
    assert job.next_packet() is None
    job.observe(ack(40, session=17), NODE.address, generation=1)
    assert job.result is None
    packets = job.observe(reply(job), NODE.address, generation=1)
    assert len(packets) == 2
    receipt = gute_mode2_decrypt(packets[-1], NODE.session_key)
    assert receipt[1] == 0xBA and struct.unpack_from('<I', receipt, 12)[0] == 41
    assert job.result.status == 'response' and job.result.error_code == 0
    assert job.result.sent and job.result.transport_acknowledged and job.result.enabled is None
    assert source.matches(17,1) and source.allocate() == 42


@pytest.mark.parametrize('change', [{'session':18}, {'access':124}, {'device':7000000003},
                                    {'request':99}, {'direction':1}])
def test_wrong_identity_or_request_never_confirms_lamp_write(change):
    job, _, _ = fixture(); job.next_packet()
    assert job.observe(reply(job, **change), NODE.address, generation=1) == ()
    assert job.result is None


def test_status_reply_wrong_peer_generation_or_unsent_command_cannot_complete_write():
    job, _, now = fixture()
    assert not job.observe(reply(job), NODE.address, generation=1)
    job.next_packet()
    assert not job.observe(response(job), NODE.address, generation=1)  # Type12 is not type11.
    assert not job.observe(reply(job), ('192.0.2.99', 1), generation=1)
    assert not job.observe(reply(job), NODE.address, generation=2)
    now[0] = 2
    assert not job.observe(reply(job), NODE.address, generation=1)
    assert job.next_packet() is None and job.result.status == 'timeout' and job.result.sent


@pytest.mark.parametrize('error', [True, None, '0', 1 << 40])
def test_invalid_application_error_cannot_claim_acceptance(error):
    job, _, _ = fixture(); job.next_packet()
    job.observe(reply(job, error=error), NODE.address, generation=1)
    assert job.result.status == 'invalid_response'


def test_explicit_rejection_or_missing_error_is_not_a_success():
    job, _, _ = fixture(); job.next_packet()
    job.observe(reply(job, error=7), NODE.address, generation=1)
    assert job.result.status == 'rejected' and job.result.error_code == 7
    other, _, _ = fixture(); other.next_packet()
    other.observe(reply(other, omit_error=True), NODE.address, generation=1)
    assert other.result.status == 'invalid_response'


def test_ack_cannot_extend_deadline_and_cancellation_never_replays_or_closes_namespace():
    job, source, now = fixture(); job.next_packet(); now[0] = 1.9
    job.observe(ack(40, session=17), NODE.address, generation=1)
    now[0] = 2
    assert job.next_packet() is None and job.result.status == 'timeout'
    assert source.matches(17,1)
    other, source, _ = fixture(); other.cancel()
    assert other.next_packet() is None and not other.result.sent
    assert source.matches(17,1)


@pytest.mark.parametrize('value', [1, 0, 'true', None, {}])
def test_write_spec_rejects_non_boolean_values(value):
    with pytest.raises(ValueError): LampWriteSpec(123, 7000000002, value)


def test_read_and_write_entry_points_cannot_swap_specs_or_consume_sequences():
    source = BrokerSequences(17,1,40)
    with pytest.raises(ValueError):
        LampReadJob(NODE,source,LampWriteSpec(123,7000000002,True),generation=1,deadline=2,clock=lambda:0)
    with pytest.raises(ValueError):
        LampWriteJob(NODE,source,LampReadSpec(123,7000000002),generation=1,deadline=2,clock=lambda:0)
    assert source.allocate() == 40


@pytest.mark.parametrize('deadline',[True,0,-1,3,float('nan'),float('inf')])
def test_invalid_write_deadline_consumes_no_sequence(deadline):
    source=BrokerSequences(17,1,40)
    with pytest.raises(ValueError):
        LampWriteJob(NODE,source,LampWriteSpec(123,7000000002,True),
            generation=1,deadline=deadline,clock=lambda:0)
    assert source.allocate()==40


@pytest.mark.parametrize('access,device',[(True,1),(1,True),(-1,1),(1,0),(1,1<<64)])
def test_write_spec_rejects_invalid_target_before_network(access,device):
    with pytest.raises(ValueError):LampWriteSpec(access,device,True)
