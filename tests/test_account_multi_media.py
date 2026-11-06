"""Offline broker concurrency: no login, media socket or camera operation."""

import struct
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.account_output_budget import (
    AccountOutputBudget,
    OutputExhausted,
)
from backend.app.drivers.yoosee.p2p.broker_account import BrokerAccount
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteSpec
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_broker_reads import DEVICE, NODE, PATH, response
from tests.test_broker_transactions import ack
from tests.test_tcp_route import advertisement


def setup(capacity=3, packets=512):
    now = [0.0]
    def clock():
        return now[0]
    account = BrokerAccount(NODE, 1, "192.0.2.1", 1234, clock=clock, media_capacity=capacity)
    output = AccountOutputBudget(packets, packets * 2048, media_capacity=capacity)
    return account, output, now


def prepare(account, output, index):
    return account.prepare_media(MediaRouteSpec(123, replace(DEVICE, device_id=100 + index)), output)


def sequence(packet):
    return struct.unpack_from("<I", gute_mode2_decrypt(packet.wire, NODE.session_key), 12)[0]


def deliver(account, frame, *, peer=NODE.address, generation=1):
    return account.receive(frame, peer, generation=generation)


def relay(job):
    frame = bytearray(advertisement(link=job.attempt.link_id))
    struct.pack_into("<Q", frame, 4, NODE.session_id)
    return gute_mode2_decrypt(finish_mode2(frame, NODE.session_key), NODE.session_key)


def close(account, job):
    job.cancel()
    packet = job.next_packet()
    assert packet.kind == "close"
    deliver(account, ack(sequence(packet), session=NODE.session_id, kind=0xB9))
    assert job.result.broker_release


def test_three_routes_reads_and_heartbeat_share_one_sequence_namespace():
    account, output, now = setup()
    jobs = [prepare(account, output, index) for index in range(3)]
    sequences = [sequence(job.next_packet()) for job in jobs]
    read = account.submit_read(DEVICE.device_id, PATH, 2)
    sequences.append(read.request.ticket.sequence)
    deliver(account, response(read.request))
    # Relay replies are consumed by the account, not three independent readers.
    for job in reversed(jobs):
        deliver(account, relay(job))
        assert job.endpoint == ("8.8.8.8", 0x584D)
    for job in reversed(jobs):
        job.cancel()
        packet = job.next_packet()
        sequences.append(sequence(packet))
        deliver(account, ack(sequence(packet), session=NODE.session_id, kind=0xB9))
    now[0] = 50
    beat = account.tick()
    sequences.append(struct.unpack_from("<I", gute_mode2_decrypt(beat.outgoing[0], NODE.session_key), 12)[0])
    assert len(sequences) == len(set(sequences)) == 8
    assert all(job.result.broker_release for job in jobs)
    assert not account.media_cleanup_pending


@pytest.mark.parametrize("fault", ["peer", "generation", "link", "session"])
def test_relay_response_cannot_cross_routes_or_account_identity(fault):
    account, output, _ = setup()
    first, second = [prepare(account, output, index) for index in range(2)]
    for job in (first, second):
        job.next_packet()
    frame = relay(first)
    if fault in ("link", "session"):
        frame = bytearray(frame)
        struct.pack_into("<I" if fault == "link" else "<Q", frame,
                         28 if fault == "link" else 4, 999999)
        frame = gute_mode2_decrypt(finish_mode2(frame, NODE.session_key), NODE.session_key)
    deliver(account, frame, peer=("192.0.2.254", 1) if fault == "peer" else NODE.address,
            generation=2 if fault == "generation" else 1)
    assert first.endpoint is second.endpoint is None
    deliver(account, relay(first))
    assert first.endpoint is not None and second.endpoint is None


def test_clean_close_releases_only_its_slot_without_stopping_other_routes():
    account, output, _ = setup()
    jobs = [prepare(account, output, index) for index in range(3)]
    for job in jobs:
        job.next_packet()
    with pytest.raises(ValueError, match="capacity"):
        prepare(account, output, 3)
    close(account, jobs[1])
    replacement = prepare(account, output, 1)
    assert replacement is not jobs[1]
    assert jobs[0].result is jobs[2].result is None
    assert account.state == "active"


def test_duplicate_and_default_single_route_admission_stay_closed():
    account, output, _ = setup(1)
    prepare(account, output, 0)
    for index in (0, 1):
        with pytest.raises(ValueError, match="capacity"):
            prepare(account, output, index)


def test_different_output_owner_cannot_bypass_shared_cleanup_accounting():
    account, output, _ = setup()
    prepare(account, output, 0)
    with pytest.raises(ValueError, match="output owner"):
        prepare(account, AccountOutputBudget(media_capacity=3), 1)


def test_unconfirmed_route_release_fences_all_routes_and_new_admission():
    account, output, now = setup()
    first, second = [prepare(account, output, index) for index in range(2)]
    for job in (first, second):
        job.next_packet()
    first.cancel()
    first.next_packet()
    now[0] = 1.5
    first.next_packet()
    assert first.result.opened and not first.result.broker_release
    assert account.tick().retired == "transport"
    assert second.next_packet().kind == "close"
    with pytest.raises(ValueError):
        prepare(account, output, 2)


def test_drain_waits_for_all_close_receipts_not_just_the_first():
    account, output, _ = setup()
    jobs = [prepare(account, output, index) for index in range(3)]
    for job in jobs:
        job.next_packet()
    assert account.drain().retired is None
    for job in jobs:
        packet = job.next_packet()
        assert packet.kind == "close"
        deliver(account, ack(sequence(packet), session=NODE.session_id, kind=0xB9))
    assert not account.media_cleanup_pending
    assert account.tick().retired == "drained"


def test_three_media_reserves_do_not_borrow_ptz_or_siren_cleanup():
    output = AccountOutputBudget(24, 24 * 4096, media_capacity=3)
    media = [output.reserve_media() for _ in range(3)]
    ptz, siren = output.reserve_ptz(), output.reserve_siren()
    with pytest.raises(OutputExhausted):
        output.charge(1)
    for reserve in media:
        reserve.charge("open", 4096)
        reserve.charge("close", 4096)
    for kind, count in (("start", 1), ("release", 3), ("receipt", 2)):
        for _ in range(count):
            ptz.charge(kind, 4096)
    for kind, count in (("on", 1), ("off", 2), ("read", 3), ("receipt", 6)):
        for _ in range(count):
            siren.charge(kind, 4096)
    with pytest.raises(OutputExhausted):
        output.charge(1)


def test_link_ids_remain_unique_even_when_random_values_repeat(monkeypatch):
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.media_route_job.secrets.randbelow", lambda _: 10)
    account, output, _ = setup()
    first = prepare(account, output, 0)
    old_link = first.attempt.link_id
    first.next_packet()
    late_relay = relay(first)
    close(account, first)
    second = prepare(account, output, 0)
    assert second.attempt.link_id != old_link
    second.next_packet()
    deliver(account, late_relay)
    assert second.endpoint is None


def test_close_receipt_releases_only_its_exact_route():
    account, output, _ = setup()
    first, second = [prepare(account, output, index) for index in range(2)]
    for job in (first, second):
        job.next_packet()
        job.cancel()
    first_close, second_close = first.next_packet(), second.next_packet()
    deliver(account, ack(sequence(first_close), session=NODE.session_id, kind=0xB9))
    assert first.result.broker_release and second.result is None
    deliver(account, ack(sequence(second_close), session=NODE.session_id, kind=0xB9))
    assert second.result.broker_release


def test_closing_one_reserve_does_not_release_other_media_reservations():
    output = AccountOutputBudget(media_capacity=3)
    first, second, third = [output.reserve_media() for _ in range(3)]
    first.close()
    replacement = output.reserve_media()
    first.close()  # Stale/idempotent close cannot remove the new reservation.
    with pytest.raises(OutputExhausted):
        output.reserve_media()
    with pytest.raises(OutputExhausted):
        first.charge("close", 1)
    for reserve in (second, third, replacement):
        reserve.charge("close", 1)


@pytest.mark.parametrize("capacity", [0, -1, True, 1.0, "3"])
def test_invalid_capacity_is_rejected(capacity):
    with pytest.raises(ValueError):
        AccountOutputBudget(media_capacity=capacity)
    with pytest.raises(ValueError):
        BrokerAccount(NODE, 1, "192.0.2.1", 1234, media_capacity=capacity)
