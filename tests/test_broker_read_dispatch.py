import pytest

from backend.app.drivers.yoosee.p2p.broker_read_dispatch import (
    BrokerReadDispatcher,
    ReadDispatchError,
)
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from tests.test_broker_model_reply import DEVICE, NODE
from tests.test_broker_reads import PATH, receipt, response
from tests.test_vendor_resource_service_protocol import _fragment


def wire(plain):
    return gute_mode2_encrypt(plain, NODE.session_key)


def test_read_request_and_fragmented_response_after_positive_ack():
    owner = BrokerReadDispatcher(NODE, 1)
    prepared = owner.prepare(DEVICE.device_id, PATH, 1)
    plain = gute_mode2_decrypt(prepared.wire, NODE.session_key)
    assert plain[1] == 0xB7 and PATH.encode() in plain
    assert "wire=" not in repr(prepared)
    batch = owner.receive(wire(receipt(prepared.request)), NODE.address, generation=1)
    assert not batch.completions and owner.pending_count == 1
    raw = wire(response(prepared.request))
    split = (len(raw) + 1) // 2
    batch = owner.receive(_fragment(1, 2, raw, split), NODE.address, generation=1)
    assert len(batch.acknowledgements) == 1 and not batch.completions
    batch = owner.receive(_fragment(0, 2, raw, split), NODE.address, generation=1)
    assert batch.completions[0].status == "response"
    assert batch.completions[0].transport_result == 0 and owner.pending_count == 0
    assert not owner.receive(raw, NODE.address, generation=1).completions
    owner.close()


def test_wrong_peer_generation_and_silence_preserve_explicit_timeout():
    now = [0.0]
    owner = BrokerReadDispatcher(NODE, 1, clock=lambda: now[0])
    prepared = owner.prepare(DEVICE.device_id, PATH, 1)
    raw = wire(response(prepared.request))
    assert not owner.receive(raw, (NODE.address[0], 1), generation=1).completions
    assert not owner.receive(raw, NODE.address, generation=2).completions
    now[0] = 1
    assert owner.poll()[0].status == "timeout"
    assert not owner.receive(raw, NODE.address, generation=1).completions
    now[0] = 15
    assert owner.poll() == () and owner.closed
    with pytest.raises(ValueError):
        owner.prepare(DEVICE.device_id, PATH, 1)


def test_malformed_fragment_closes_session_and_returns_cancelled_waiters():
    owner = BrokerReadDispatcher(NODE, 1)
    request = owner.prepare(DEVICE.device_id, PATH, 1).request
    with pytest.raises(ReadDispatchError) as error:
        owner.receive(b"\x70\x01", NODE.address, generation=1)
    assert error.value.cancelled[0].request == request
    assert error.value.cancelled[0].status == "cancelled"
    assert owner.closed and owner.pending_count == 0 and owner._node is None


def test_no_late_registration_or_automatic_resend():
    now = [0.0]
    owner = BrokerReadDispatcher(NODE, 1, clock=lambda: now[0])
    prepared = owner.prepare(DEVICE.device_id, PATH, 1)
    assert owner.cancel(prepared.request.ticket).status == "cancelled"
    assert owner.poll() == ()
    now[0] = 14.5
    with pytest.raises(ValueError, match="remaining"):
        owner.prepare(DEVICE.device_id, PATH, 1)
    assert owner.pending_count == 0
    owner.close()


def test_ack_construction_failure_does_not_lose_application_completion(monkeypatch):
    owner = BrokerReadDispatcher(NODE, 1)
    request = owner.prepare(DEVICE.device_id, PATH, 1).request
    def fail(wire, peer, *, inspect_message):
        inspect_message(response(request))
        raise ValueError("synthetic ACK construction error")
    monkeypatch.setattr(owner._maintenance, "receive", fail)
    with pytest.raises(ReadDispatchError) as error:
        owner.receive(b"", NODE.address, generation=1)
    assert error.value.cancelled[0].request == request
    assert owner.closed and owner.pending_count == 0
