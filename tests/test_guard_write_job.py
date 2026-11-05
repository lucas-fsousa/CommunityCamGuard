import json

import pytest

from backend.app.drivers.contracts import WeeklySchedule
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.model_control_inbox import ControlWriteInbox
from backend.app.drivers.yoosee.p2p.model_control_job import ControlWriteJob, ControlWriteResult
from backend.app.drivers.yoosee.p2p.model_control_specs import (
    GuardScheduleWriteSpec,
    GuardWriteSpec,
    VolumeWriteSpec,
)
from backend.app.drivers.yoosee.p2p.smart_protection_schedule import (
    native_smart_protection_schedule,
)
from tests.test_lamp_read_job import NODE
from tests.test_volume_write_job import response

PLAN = WeeklySchedule('23:30', '06:15', ('sun', 'mon', 'fri'))


@pytest.mark.parametrize('spec', [GuardWriteSpec(7000000002, False), GuardWriteSpec(7000000002, True),
                                 GuardScheduleWriteSpec(7000000002, PLAN)])
def test_guard_jobs_send_one_fixed_write_and_require_application_confirmation(spec):
    seq = BrokerSequences(NODE.session_id, 1, 40)
    job = ControlWriteJob(NODE, seq, spec, generation=1, deadline=2, clock=lambda: 0)
    plain = gute_mode2_decrypt(job.next_packet(), NODE.session_key)
    assert plain[1] == 0xD2 and b'Action.' not in plain
    if isinstance(spec, GuardWriteSpec):
        assert b'ProWritable.guardParm.setVal.enable\0' in plain
        assert plain.endswith(str(int(spec.enabled)).encode() + b'\0')
        assert b'plan' not in plain
    else:
        assert b'ProWritable.guardParm.setVal.plan\0' in plain and b'enable' not in plain
        encoded = json.dumps(native_smart_protection_schedule(PLAN), separators=(',', ':')).encode()
        assert plain.endswith(encoded + b'\0')
    assert job.next_packet() is None and job.result is None
    job.observe(response(job), NODE.address, generation=1)
    assert job.result == ControlWriteResult('response', 0, False)
    assert seq.matches(NODE.session_id, 1)


@pytest.mark.parametrize('value', [1, 0, 'true', None, {}])
def test_guard_spec_rejects_non_booleans(value):
    with pytest.raises(ValueError): GuardWriteSpec(123, value)


@pytest.mark.parametrize('device', [True, 0, -1, 1 << 64, '123'])
def test_guard_specs_reject_invalid_target(device):
    with pytest.raises(ValueError): GuardWriteSpec(device, True)
    with pytest.raises(ValueError): GuardScheduleWriteSpec(device, PLAN)


@pytest.mark.parametrize('value', [{}, '23:30', None, WeeklySchedule('23:30','06:15',['mon'])])
def test_plan_spec_requires_immutable_domain_value(value):
    with pytest.raises(ValueError): GuardScheduleWriteSpec(123, value)


def test_guard_plan_and_volume_share_one_slot_until_cancelled_claim_is_retired():
    inbox = ControlWriteInbox(1)
    ticket = inbox.submit(GuardScheduleWriteSpec(123, PLAN))
    assert inbox.claim() is ticket
    inbox.cancel(ticket)
    with pytest.raises(ValueError): inbox.submit(GuardWriteSpec(123, True))
    with pytest.raises(ValueError): inbox.submit(VolumeWriteSpec(123, 50))
    inbox.finish(ticket, ControlWriteResult('cancelled'))
    assert inbox.submit(GuardWriteSpec(123, True)).spec.enabled is True
