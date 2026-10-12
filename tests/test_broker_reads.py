import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_reads import BrokerReads
from backend.app.drivers.yoosee.p2p.broker_transactions import MAX_PENDING, ReceiptTicket
from tests.test_broker_model_reply import DEVICE, NODE
from tests.test_broker_transactions import ack
from tests.test_vendor_p2p_model_session import _response

PATH = "ProConst._productInfo"


def receipt(request, result=0):
    return ack(sequence=request.ticket.sequence, kind=0xB7, session=NODE.session_id, result=result)


def response(request, error=0):
    frame = bytearray(_response(NODE, DEVICE, {"private": "value"}, sequence=request.ticket.sequence))
    struct.pack_into("<H", frame, 36, error)
    return bytes(frame)


@pytest.mark.parametrize("ack_first", [False, True])
def test_response_completes_once_in_either_order(ack_first):
    owner = BrokerReads(NODE.session_id, 1, 40)
    request = owner.register(DEVICE.device_id, PATH, 1)
    if ack_first:
        assert owner.accept(receipt(request), generation=1) is None
        assert owner.pending_count == 1
    result = owner.accept(response(request), generation=1)
    assert result.status == "response" and result.error_code == 0
    assert result.transport_result == (0 if ack_first else None)
    assert result.value == {"private": "value"} and "private" not in repr(result)
    assert owner.accept(receipt(request), generation=1) is None
    assert owner.accept(response(request), generation=1) is None
    assert owner.pending_count == 0


def test_positive_receipts_cannot_free_application_capacity_or_extend_timeout():
    now = [0.0]
    owner = BrokerReads(NODE.session_id, 1, 40, clock=lambda: now[0])
    requests = [owner.register(DEVICE.device_id, PATH, 1) for _ in range(MAX_PENDING)]
    now[0] = 0.9
    for request in requests:
        assert owner.accept(receipt(request), generation=1) is None
    with pytest.raises(ValueError, match="capacity"):
        owner.register(DEVICE.device_id, PATH, 1)
    now[0] = 1.0
    assert owner.accept(response(requests[0]), generation=1) is None
    results = owner.poll()
    assert len(results) == MAX_PENDING
    assert all(r.status == "timeout" and r.transport_result == 0 for r in results)
    assert owner.poll() == () and owner.pending_count == 0
    assert owner.register(DEVICE.device_id, PATH, 1).ticket.sequence == 40 + MAX_PENDING


@pytest.mark.parametrize("transport", [False, True])
def test_negative_transport_and_camera_results_are_distinct(transport):
    owner = BrokerReads(NODE.session_id, 1, 40)
    request = owner.register(DEVICE.device_id, PATH, 1)
    result = owner.accept(receipt(request, 4) if transport else response(request, 20001), generation=1)
    assert result.status == ("transport_rejected" if transport else "response")
    assert result.transport_result == (4 if transport else None)
    assert result.error_code == (None if transport else 20001)
    assert owner.pending_count == 0


def test_cancellation_and_close_emit_once_and_invalidate_late_input():
    owner = BrokerReads(NODE.session_id, 1, 40)
    first, second = [owner.register(DEVICE.device_id, PATH, 1) for _ in range(2)]
    assert owner.cancel(ReceiptTicket(2, first.ticket.sequence, 0xB7)) is None
    assert owner.cancel(first.ticket).status == "cancelled"
    assert owner.cancel(first.ticket) is None
    assert owner.close()[0].request == second
    assert owner.close() == () and owner.poll() == ()
    assert owner.accept(response(second), generation=1) is None
    with pytest.raises(ValueError, match="closed"):
        owner.register(DEVICE.device_id, PATH, 1)


def test_wrong_device_generation_and_reordered_reads():
    owner = BrokerReads(NODE.session_id, 1, 40)
    first = owner.register(DEVICE.device_id + 1, PATH, 1)
    second = owner.register(DEVICE.device_id, PATH, 1)
    assert owner.accept(response(first), generation=1) is None
    assert owner.accept(response(second), generation=2) is None
    assert owner.accept(response(second), generation=1).request == second
    assert owner.pending_count == 1
    assert owner.close()[0].request == first


@pytest.mark.parametrize("device,path,timeout", [(0, PATH, 1), (True, PATH, 1),
    (1, "ProWrite.reboot", 1), (1, [], 1), (1, PATH, float("inf")), (1, PATH, 11)])
def test_invalid_reads_do_not_allocate_or_consume_sequence(device, path, timeout):
    owner = BrokerReads(NODE.session_id, 1, 40)
    with pytest.raises(ValueError):
        owner.register(device, path, timeout)
    assert owner.pending_count == 0
    assert owner.register(DEVICE.device_id, PATH, 1).ticket.sequence == 40
