import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import AccountOutputBudget
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from tests.test_broker_account import account, read
from tests.test_broker_reads import NODE


def prepare(owner, **changes):
    return owner.prepare_ptz(AccountOutputBudget(), **(dict(access_id=123,
        device_id=456, direction="right", reviewed_directions=frozenset({"right"})) | changes))


def test_reads_motion_and_heartbeat_share_one_sequence_namespace():
    owner, now = account()
    first = read(owner).request.ticket.sequence
    job = prepare(owner)
    start = gute_mode2_decrypt(job.next_packet().wire, NODE.session_key)
    assert struct.unpack_from("<I", start, 12)[0] == first + 1
    assert read(owner).request.ticket.sequence == first + 4
    now[0] = 50
    beat, = owner.tick().outgoing
    assert struct.unpack_from("<I", gute_mode2_decrypt(beat, NODE.session_key), 12)[0] == first + 5
    owner.retire()


def test_second_job_cannot_bypass_admission_with_fresh_output_budget():
    owner, _ = account()
    job = prepare(owner)
    with pytest.raises(ValueError): prepare(owner)
    assert job.next_packet().kind == "start"


@pytest.mark.parametrize("started", [False, True])
def test_retirement_cancels_job_but_preserves_old_socket_cleanup(started):
    owner, now = account()
    job = prepare(owner)
    if started: job.next_packet()
    owner.retire()
    assert owner.motion_cleanup_pending
    if started:
        assert job.next_packet().kind == "release"
        now[0] = 2
        job.next_packet()
        assert job.result.reason == "release_unconfirmed"
    else:
        assert job.next_packet() is None and job.result.reason == "cancelled"
    assert not owner.motion_cleanup_pending
    with pytest.raises(ValueError): prepare(owner)


def test_drain_waits_for_motion_and_repeated_drain_does_not_extend_deadline():
    owner, now = account()
    job = prepare(owner)
    job.next_packet()
    assert owner.drain().retired is None
    assert job.next_packet().kind == "release"
    now[0] = 1.9
    assert owner.drain().retired is None
    now[0] = 2
    assert owner.tick().retired == "drain_timeout"
    assert owner.motion_cleanup_pending  # account retirement is NOT socket close
    job.next_packet()
    assert not owner.motion_cleanup_pending


def test_unknown_release_blocks_reads_and_new_motion_then_retires():
    owner, now = account()
    job = prepare(owner)
    job.next_packet()
    now[0] = 0.2
    job.next_packet()
    now[0] = 2.3
    job.next_packet()
    with pytest.raises(ValueError): read(owner)
    with pytest.raises(ValueError): prepare(owner)
    assert owner.tick().retired == "transport"


def test_cancelled_unstarted_job_allows_new_admission():
    owner, _ = account()
    job = prepare(owner)
    job.cancel()
    job.next_packet()
    assert prepare(owner).next_packet().kind == "start"


def test_near_lifetime_cannot_admit_new_gesture():
    owner, now = account()
    now[0] = 117
    with pytest.raises(ValueError): prepare(owner)
