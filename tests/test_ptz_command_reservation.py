import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p import ptz_command_reservation as commands
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from tests.test_broker_reads import DEVICE, NODE


def reserve(source, **kwargs):
    args = dict(generation=1, access_id=123, device_id=DEVICE.device_id,
                direction="right", reviewed_directions=frozenset({"right", "left"}))
    args.update(kwargs)
    return commands.reserve_ptz_commands(NODE, source, **args)


def source(next_sequence=40):
    return BrokerSequences(NODE.session_id, 1, next_sequence)


def decode(wire):
    plain = gute_mode2_decrypt(wire, NODE.session_key)
    return struct.unpack_from("<I", plain, 12)[0], json.loads(plain[0x3C:].rstrip(b"\x00"))


def test_commands_reserve_stop_and_receipt_before_other_consumers():
    seq = source()
    prepared = reserve(seq)
    assert seq.allocate() == 43
    start_id, start = decode(prepared.take_start())
    release_wire = prepared.take_release()
    release_id, release = decode(release_wire)
    assert (start_id, release_id, prepared.receipt_sequence) == (40, 41, 42)
    assert start == {"type": 2, "data": {"dir": 1, "touchType": 0}}
    assert release == {"type": 2, "data": {"dir": 1, "touchType": 1}}
    assert prepared.take_release() == prepared.take_release() == release_wire
    with pytest.raises(ValueError): prepared.take_release()
    with pytest.raises(ValueError): prepared.take_start()


def test_ambiguous_start_and_namespace_retirement_still_allow_pinned_stop():
    seq = source()
    prepared = reserve(seq)
    prepared.take_start()  # Transport may fail after this: no second START.
    seq.close()
    prepared.cancel_start()
    with pytest.raises(ValueError): prepared.take_start()
    assert decode(prepared.take_release())[1]["data"]["touchType"] == 1


@pytest.mark.parametrize("cancel", [True, False])
def test_pre_start_cancel_or_retirement_never_grants_motion(cancel):
    seq = source()
    prepared = reserve(seq)
    if cancel: prepared.cancel_start()
    else: seq.close()
    with pytest.raises(ValueError): prepared.take_start()
    with pytest.raises(ValueError): prepared.take_release()


def test_insufficient_sequence_capacity_does_not_partially_reserve():
    seq = source(0xFFFFFFFE)
    with pytest.raises(ValueError, match="exhausted"): reserve(seq)
    assert seq.allocate() == 0xFFFFFFFE


def test_exact_end_of_namespace_has_reserved_stop_without_wrap():
    seq = source(0xFFFFFFFD)
    prepared = reserve(seq)
    prepared.take_start()
    with pytest.raises(ValueError): seq.allocate()
    assert decode(prepared.take_release())[0] == 0xFFFFFFFE
    assert prepared.receipt_sequence == 0xFFFFFFFF


@pytest.mark.parametrize("kwargs", [{"generation": 2}, {"generation": True},
    {"access_id": 0}, {"device_id": True}, {"direction": "up"},
    {"reviewed_directions": frozenset({"right", "patrol"})},
    {"reviewed_directions": {"right"}}])
def test_invalid_profile_or_identity_consumes_nothing(kwargs):
    seq = source()
    with pytest.raises(ValueError): reserve(seq, **kwargs)
    assert seq.allocate() == 40


def test_builder_failure_never_recycles_reserved_ids(monkeypatch):
    seq = source()
    def fail(*args, **kwargs): raise ValueError("cannot encode")
    monkeypatch.setattr(commands, "build_ptz_request", fail)
    with pytest.raises(ValueError): reserve(seq)
    assert seq.allocate() == 43


@pytest.mark.parametrize("count", [0, -1, 65, True, 1.5])
def test_invalid_group_size_consumes_nothing(count):
    seq = source()
    with pytest.raises(ValueError): seq.reserve(count)
    assert seq.allocate() == 40
