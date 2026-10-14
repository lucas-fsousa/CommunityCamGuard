import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import (
    AccountOutputBudget,
    OutputExhausted,
)
from backend.app.drivers.yoosee.p2p.ptz_command_reservation import ReleaseCorrelation
from backend.app.drivers.yoosee.p2p.ptz_release_delivery import ReleaseDelivery
from tests.test_ptz_protocol import NODE, reply


def tracker(clock=lambda: 0):
    return ReleaseDelivery(NODE, generation=1, access_id=123, device_id=456,
                           correlation=ReleaseCorrelation(18, 44, 55), deadline=2, clock=clock)


def test_ordinary_flood_cannot_consume_start_stop_or_receipts():
    budget = AccountOutputBudget(packets=8, octets=8 * 4096)
    reserved = budget.reserve_ptz()
    budget.charge(4096)
    budget.charge(4096)
    with pytest.raises(OutputExhausted): budget.charge(1)
    reserved.charge("start", 4096)
    with pytest.raises(OutputExhausted): reserved.charge("start", 1)
    for _ in range(3): reserved.charge("release", 4096)
    for _ in range(2): reserved.charge("receipt", 4096)
    reserved.close()
    with pytest.raises(OutputExhausted): budget.charge(1)


@pytest.mark.parametrize("packets,octets", [(5, 999999), (512, 6 * 4096 - 1)])
def test_insufficient_capacity_rejects_before_motion(packets, octets):
    budget = AccountOutputBudget(packets, octets)
    with pytest.raises(OutputExhausted): budget.reserve_ptz()
    budget.charge(1)


def test_only_one_reservation_and_no_refund_of_ambiguous_attempt():
    budget = AccountOutputBudget(7, 7 * 4096)
    first = budget.reserve_ptz()
    with pytest.raises(OutputExhausted): budget.reserve_ptz()
    first.charge("start", 4096)  # A failed transport send is still charged.
    first.close()
    first.close()
    second = budget.reserve_ptz()
    with pytest.raises(OutputExhausted): first.charge("release", 1)
    with pytest.raises(OutputExhausted): budget.charge(1)
    second.close()


@pytest.mark.parametrize("kind,size", [("other", 1), ("release", 4097), ("release", 0),
                                       ("release", True)])
def test_invalid_reserved_send_preserves_credit(kind, size):
    reserve = AccountOutputBudget().reserve_ptz()
    with pytest.raises(OutputExhausted): reserve.charge(kind, size)
    assert reserve.remaining == 6


def test_receipts_alone_need_both_layers_and_error_is_sticky():
    item = tracker()
    assert item.observe(reply(ack=True), NODE.address, generation=1)
    assert not item.confirmed
    item.observe(reply(kind=0xBA), NODE.address, generation=1)
    assert item.confirmed
    item.observe(reply(value={"type": 2, "err": 7}), NODE.address, generation=1)
    assert not item.confirmed
    item.observe(reply(), NODE.address, generation=1)
    assert not item.confirmed


def test_application_response_can_precede_receipts():
    item = tracker()
    item.observe(reply(), NODE.address, generation=1)
    assert item.confirmed


@pytest.mark.parametrize("changes", [{"session": 10}, {"source": 999}, {"destination": 999},
                                    {"request": 999}, {"mode": 1}])
def test_wrong_identity_never_confirms(changes):
    item = tracker()
    assert item.observe(reply(**changes), NODE.address, generation=1) is None
    assert not item.confirmed


def test_old_generation_wrong_peer_and_silence_do_not_confirm():
    now = [0]
    item = tracker(lambda: now[0])
    assert item.observe(reply(), NODE.address, generation=2) is None
    assert item.observe(reply(), ("192.0.2.5", 19800), generation=1) is None
    now[0] = 2
    assert item.observe(reply(), NODE.address, generation=1) is None
    assert not item.confirmed


def test_flood_is_bounded_even_after_positive_reply():
    item = tracker()
    item.observe(reply(), NODE.address, generation=1)
    for _ in range(64): item.observe(b"", NODE.address, generation=1)
    assert not item.confirmed


@pytest.mark.parametrize("deadline", [0, 3, float("nan"), float("inf"), True])
def test_unbounded_observation_rejected(deadline):
    with pytest.raises(ValueError):
        ReleaseDelivery(NODE, generation=1, access_id=123, device_id=456,
                        correlation=ReleaseCorrelation(18, 44, 55), deadline=deadline, clock=lambda: 0)
