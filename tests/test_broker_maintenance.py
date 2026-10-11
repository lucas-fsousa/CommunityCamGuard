import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_maintenance import BrokerMaintenance
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from tests.test_vendor_resource_service_protocol import _fragment

NODE = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)


def message(identity=9, ack=False):
    frame = new_header(0xAA, 32, identity, 41, (2 << 16) | (1 << 18) | (int(ack) << 20))
    frame[0] = 0x7E
    return finish_mode2(frame, NODE.session_key)


def test_current_reliable_message_gets_ack_only():
    owner = BrokerMaintenance(NODE)
    reply, = owner.receive(message(), NODE.address)
    plain = gute_mode2_decrypt(reply, NODE.session_key)
    assert plain[:2] == b"\x7e\xaa"
    assert struct.unpack_from("<I", plain, 12)[0] == 41
    assert struct.unpack_from("<I", plain, 20)[0] & (1 << 20)
    assert owner.messages == owner.acknowledgements == 1
    assert not owner.receive(message(ack=True), NODE.address)
    owner.close()
    with pytest.raises(ValueError):
        owner.receive(message(), NODE.address)


@pytest.mark.parametrize("fault", ["peer", "session", "key", "plain"])
def test_uncorrelated_input_cannot_trigger_message_ack(fault):
    owner = BrokerMaintenance(NODE)
    wire = message(identity=10 if fault == "session" else 9)
    peer = (NODE.address[0], 1) if fault == "peer" else NODE.address
    if fault == "key":
        wire = wire[:8] + bytes((wire[8] ^ 1,)) + wire[9:]
    if fault == "plain":
        wire = bytes(32)
    inspected = []
    assert not owner.receive(wire, peer, inspect_message=inspected.append)
    assert not inspected
    assert owner.messages == owner.acknowledgements == 0


@pytest.mark.parametrize("identity,expected", [(9, 1), (10, 0)])
def test_fragment_group_identity_is_not_session_identity(identity, expected):
    owner = BrokerMaintenance(NODE)
    wire = message(identity)
    # Helper uses a different outer identity; only the inner session matters.
    inspected = []
    assert len(owner.receive(_fragment(1, 2, wire, 16), NODE.address,
                             inspect_message=inspected.append)) == 1
    assert not inspected
    assert len(owner.receive(_fragment(0, 2, wire, 16), NODE.address,
                             inspect_message=inspected.append)) == 1 + expected
    assert len(inspected) == expected
    assert owner.messages == expected
    assert owner.acknowledgements == 2 + expected
    owner.close()


@pytest.mark.parametrize("fault", ["time", "count", "size", "fragment"])
def test_limits_terminate_owner(fault):
    now = [0.0]
    owner = BrokerMaintenance(NODE, clock=lambda: now[0])
    wire = message()
    if fault == "time":
        now[0] = 15
    elif fault == "count":
        for _ in range(512):
            owner.receive(b"", NODE.address)
    elif fault == "size":
        wire = bytes(4097)
    else:
        wire = b"\x70\x01"
    with pytest.raises(ValueError):
        owner.receive(wire, NODE.address)
    assert owner.closed
