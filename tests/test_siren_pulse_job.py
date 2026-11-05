import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import (
    AccountOutputBudget,
    OutputExhausted,
)
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.siren_pulse_job import SirenPulseJob, SirenPulseSpec
from backend.app.drivers.yoosee.p2p.wire import new_header
from tests.test_broker_reads import DEVICE
from tests.test_broker_transactions import ack
from tests.test_lamp_read_job import NODE
from tests.test_vendor_p2p_model_session import _response


def make(seconds=2, output=None):
    now = [0.0]
    sequences = BrokerSequences(NODE.session_id, 1, 40)
    output = output or AccountOutputBudget(clock=lambda: now[0])
    job = SirenPulseJob(NODE, sequences, output, SirenPulseSpec(123, 7000000002, seconds),
                       generation=1, clock=lambda: now[0])
    return job, sequences, output, now


def response(job, off=False, error=0):
    plain = new_header(0xAD, 54, NODE.session_id, 200, (2 << 16) | (1 << 18))
    struct.pack_into('<I', plain, 48, job._off_id if off else job._on_id)
    struct.pack_into('<H', plain, 52, error)
    return bytes(plain)


def state_response(job, state=1):
    sequence = job._probe._tickets[job._probe._index-1].sequence
    return _response(NODE, DEVICE, {'t':1, 'stVal':state}, sequence=sequence)


@pytest.mark.parametrize('seconds', [2, 5, 10])
def test_one_on_then_owner_timed_off_with_exact_application_confirmation(seconds):
    job, sequences, output, now = make(seconds)
    on = job.next_packet()
    decoded = gute_mode2_decrypt(on.wire, NODE.session_key)
    assert on.kind == 'on' and decoded[1] == 0xAC
    assert b'Action.expelCtrl.stVal\0' in decoded and decoded.endswith(b'2\0')
    assert job.next_packet() is None
    job.observe(ack(40, 0xAC, NODE.session_id), NODE.address, generation=1)
    receipts = job.observe(response(job), NODE.address, generation=1)
    assert len(receipts) == 1 and receipts[0].kind == 'receipt'
    now[0] = seconds-0.01
    assert job.next_packet() is None
    now[0] = seconds
    off = job.next_packet()
    assert off.kind == 'off' and gute_mode2_decrypt(off.wire, NODE.session_key).endswith(b'1\0')
    job.observe(ack(41, 0xAC, NODE.session_id), NODE.address, generation=1)
    assert job.result is None  # Transport ACK cannot prove silence.
    job.observe(response(job, off=True), NODE.address, generation=1)
    assert job.next_packet().kind == 'read'
    job.observe(state_response(job), NODE.address, generation=1)
    assert job.next_packet() is None and job.result.off_confirmed
    assert job.result.status == 'completed' and job.result.transport_acknowledged
    assert sequences.allocate() == 45 and output._siren is None


def test_cancel_before_on_is_silent_and_releases_capacity():
    job, _, output, _ = make()
    job.cancel()
    assert job.next_packet() is None and not job.result.start_attempted
    assert output._siren is None


@pytest.mark.parametrize('cause', ['cancel', 'send_failed', 'on_timeout', 'rejected', 'namespace'])
def test_cleanup_after_possible_on_uses_only_reserved_identical_off(cause):
    job, sequences, output, now = make()
    job.next_packet()
    if cause == 'send_failed': job.send_failed()
    elif cause == 'on_timeout': now[0] = 1.5
    elif cause == 'rejected': job.observe(response(job, error=7), NODE.address, generation=1)
    elif cause == 'namespace': sequences.close(); job.cancel()
    else: job.cancel()
    off = job.next_packet()
    assert off.kind == 'off'
    assert job.next_packet() is None
    now[0] += 0.5
    retry = job.next_packet()
    assert retry.kind == 'off' and retry.wire == off.wire
    now[0] += 2
    assert job.next_packet() is None and not job.result.off_confirmed
    assert job.result.off_attempts == 2 and output._siren is not None
    with pytest.raises(OutputExhausted): output.reserve_siren()


@pytest.mark.parametrize('fault', ['peer', 'generation', 'session', 'message', 'unsent', 'late', 'mode', 'boolean_generation'])
def test_unrelated_or_late_reply_cannot_confirm_off(fault):
    job, _, _, now = make()
    if fault != 'unsent':
        job.next_packet(); job.cancel(); job.next_packet()
    wire = bytearray(response(job, off=True))
    if fault == 'session': struct.pack_into('<Q', wire, 4, NODE.session_id+1)
    elif fault == 'message': struct.pack_into('<I', wire, 48, job._off_id ^ 2)
    elif fault == 'mode': struct.pack_into('<I', wire, 20, 1 << 16)
    elif fault == 'late': now[0] = 2
    result = job.observe(bytes(wire), ('192.0.2.99', 9) if fault == 'peer' else NODE.address,
        generation=True if fault == 'boolean_generation' else 2 if fault == 'generation' else 1)
    assert result == () and job.result is None
    if fault != 'unsent':
        now[0] = 3; job.next_packet()
        assert not job.result.off_confirmed


def test_explicit_off_error_cannot_be_overwritten_by_success():
    job, _, _, _ = make()
    job.next_packet(); job.cancel(); job.next_packet()
    job.observe(response(job, off=True, error=7), NODE.address, generation=1)
    job.observe(response(job, off=True), NODE.address, generation=1)
    job.next_packet()
    assert job.result.status == 'off_rejected' and not job.result.off_confirmed


def test_off_survives_normal_budget_exhaustion_and_namespace_retirement():
    job, sequences, output, _ = make(output=AccountOutputBudget(packets=13, octets=13*4096))
    job.next_packet()
    output.charge(32)
    with pytest.raises(OutputExhausted): output.charge(32)
    sequences.close(); job.cancel()
    assert job.next_packet().kind == 'off'
    job.observe(response(job, off=True), NODE.address, generation=1)
    assert job.next_packet().kind == 'read'
    job.observe(state_response(job), NODE.address, generation=1)
    job.next_packet()
    assert job.result.off_confirmed


def test_off_ack_without_independent_state_never_releases_the_owner():
    job, _, output, now = make()
    job.next_packet(); job.cancel(); job.next_packet()
    job.observe(response(job, off=True), NODE.address, generation=1)
    assert job.next_packet().kind == 'read'
    now[0] = 2
    job.next_packet()
    assert not job.result.off_confirmed and output._siren is not None


def test_observed_still_on_retries_only_off_and_then_confirms_fresh_state():
    job, _, _, now = make()
    job.next_packet(); job.cancel()
    off = job.next_packet()
    job.observe(response(job, off=True), NODE.address, generation=1)
    assert job.next_packet().kind == 'read'
    job.observe(state_response(job, 2), NODE.address, generation=1)
    now[0] = 0.5
    retry = job.next_packet()
    assert retry.kind == 'off' and retry.wire == off.wire
    assert job.next_packet().kind == 'read'
    job.observe(state_response(job, 1), NODE.address, generation=1)
    job.next_packet()
    assert job.result.off_confirmed and job.result.off_attempts == 2


@pytest.mark.parametrize('access,device,seconds', [(True,1,2),(0,1,2),(1,0,2),(1,True,2),(1,1,True),(1,1,1),(1,1,11)])
def test_no_raw_unbounded_action_is_admitted(access, device, seconds):
    with pytest.raises(ValueError): SirenPulseSpec(access, device, seconds)
