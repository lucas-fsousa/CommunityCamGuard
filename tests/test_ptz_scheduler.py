import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import (
    AccountOutputBudget,
    OutputExhausted,
)
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.ptz_scheduler import PtzScheduler
from tests.test_ptz_protocol import NODE, reply


@pytest.fixture
def setup():
    now = [0.0]
    budget = AccountOutputBudget()
    sequences = BrokerSequences(NODE.session_id, 1, 17)
    scheduler = PtzScheduler(NODE, sequences, budget, generation=1, access_id=123,
        device_id=456, direction="right", reviewed_directions=frozenset({"right"}),
        clock=lambda: now[0])
    return scheduler, now, budget, sequences


def response(scheduler, **changes):
    ids = scheduler._commands.release_correlation
    return reply(**({"sequence": ids.sequence, "message": ids.message_id,
                    "request": ids.request_id} | changes))


def test_finite_start_release_then_confirmed_drain(setup):
    job, now, budget, _ = setup
    assert job.next_packet().kind == "start"
    now[0] = 0.19
    assert job.next_packet() is None
    now[0] = 0.2
    assert job.next_packet().kind == "release"
    packets = job.observe(response(job), NODE.address, generation=1)
    assert [p.kind for p in packets] == ["receipt"]
    assert job.result is None and job.next_packet() is None
    now[0] = 0.23
    assert job.next_packet() is None and job.result.release_delivery_confirmed
    assert job.result.release_attempts == 1
    budget.reserve_ptz().close()
    assert job.observe(response(job), NODE.address, generation=1) == ()


@pytest.mark.parametrize("mode", ["cancel", "expired", "retired"])
def test_no_start_when_cancelled_expired_or_retired(setup, mode):
    job, now, budget, sequences = setup
    if mode == "cancel": job.cancel()
    if mode == "expired": now[0] = 1
    if mode == "retired": sequences.close()
    assert job.next_packet() is None and not job.result.start_attempted
    budget.reserve_ptz().close()


def test_ambiguous_start_failure_forces_stop_without_repeating_start(setup):
    job, now, budget, sequences = setup
    assert job.next_packet().kind == "start"
    job.send_failed()
    sequences.close()
    stop = job.next_packet()
    assert stop.kind == "release"
    now[0] = 0.5
    assert job.next_packet().wire == stop.wire
    now[0] = 1.0
    assert job.next_packet().wire == stop.wire
    now[0] = 1.5
    assert job.next_packet() is None
    now[0] = 2.0
    assert job.next_packet() is None
    assert job.result.reason == "release_unconfirmed"
    assert job.result.send_failed and job.result.release_attempts == 3
    with pytest.raises(OutputExhausted): budget.reserve_ptz()


def test_shutdown_cancellation_shortens_motion_without_extending_cleanup(setup):
    job, now, _, _ = setup
    job.next_packet()
    now[0] = 0.01
    job.cancel()
    assert job.next_packet().kind == "release"
    now[0] = 0.8
    job.cancel()
    job.next_packet()
    now[0] = 2.02
    assert job.next_packet() is None
    assert job.result.reason == "release_unconfirmed"


def test_contradictory_error_during_settle_wins_and_duplicates_do_not_create_acks(setup):
    job, now, _, _ = setup
    job.next_packet()
    now[0] = 0.2
    job.next_packet()
    assert job.observe(response(job), NODE.address, generation=1)
    now[0] = 0.21
    assert job.observe(response(job, value={"type": 2, "err": 1}), NODE.address, generation=1) == ()
    now[0] = 0.23
    assert job.next_packet() is None and job.result is None
    now[0] = 2.3
    job.next_packet()
    assert not job.result.release_delivery_confirmed


def test_stale_generation_and_wrong_peer_cannot_confirm(setup):
    job, now, _, _ = setup
    job.next_packet()
    now[0] = 0.2
    job.next_packet()
    assert job.observe(response(job), NODE.address, generation=2) == ()
    assert job.observe(response(job), ("192.0.2.99", 19800), generation=1) == ()
    now[0] = 2.3
    job.next_packet()
    assert not job.result.release_delivery_confirmed


def test_two_reserved_receipts_for_reliable_application_response(setup):
    job, now, _, _ = setup
    job.next_packet()
    now[0] = 0.2
    job.next_packet()
    wire = bytearray(response(job))
    struct.pack_into("<I", wire, 20, (2 << 16) | (1 << 18))
    packets = job.observe(bytes(wire), NODE.address, generation=1)
    assert len(packets) == 2 and all(p.kind == "receipt" for p in packets)
    assert job.observe(bytes(wire), NODE.address, generation=1) == ()
    assert all("wire=" not in repr(p) for p in packets)


def test_failed_admission_releases_output_without_sending():
    budget = AccountOutputBudget()
    sequences = BrokerSequences(NODE.session_id, 1, 0xFFFFFFFF)
    with pytest.raises(ValueError):
        PtzScheduler(NODE, sequences, budget, generation=1, access_id=123, device_id=456,
                     direction="right", reviewed_directions=frozenset({"right"}))
    budget.reserve_ptz().close()
    assert sequences.allocate() == 0xFFFFFFFF


def test_ordinary_budget_exhaustion_cannot_starve_stop(setup):
    job, _, budget, _ = setup
    job.next_packet()
    for _ in range(506): budget.charge(1)
    with pytest.raises(OutputExhausted): budget.charge(1)
    job.cancel()
    assert job.next_packet().kind == "release"
