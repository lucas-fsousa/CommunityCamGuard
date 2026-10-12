import struct

import pytest

from backend.app.drivers.yoosee.p2p.broker_model_reply import match_model_reply
from backend.app.drivers.yoosee.p2p.broker_transactions import ReceiptTicket
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt, gute_mode2_encrypt
from tests.test_vendor_p2p_model_session import _response

NODE = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
DEVICE = OnlineDevice(7000000002, 1, False, 1, bytes(16))
TICKET = ReceiptTicket(1, 18, 0xB7)


def match(frame, ticket=TICKET, generation=1):
    return match_model_reply(frame, ticket, generation=generation,
                             session_id=NODE.session_id, device_id=DEVICE.device_id)


def test_embedded_request_sequence_not_outer_sequence_or_checksum():
    plain = _response(NODE, DEVICE, {"private": "value"})
    wire = gute_mode2_encrypt(plain, NODE.session_key)
    reply = match(gute_mode2_decrypt(wire, NODE.session_key))
    assert reply.ticket == TICKET and reply.error_code == 0
    assert reply.value == {"private": "value"} and "private" not in repr(reply)
    assert match(plain, ReceiptTicket(1, 900, 0xB7)) is None


@pytest.mark.parametrize("fault", ["session", "device", "kind", "ack", "mode", "request",
                                     "generation", "ticket_kind", "report", "json", "length"])
def test_wrong_identity_and_receipt_types_cannot_be_model_completion(fault):
    frame = bytearray(_response(NODE, DEVICE, {"value": 1}))
    if fault == "session": struct.pack_into("<Q", frame, 4, 10)
    if fault == "device": struct.pack_into("<Q", frame, 24, DEVICE.device_id + 1)
    if fault == "kind": frame[1] = 0xB7
    if fault == "report": frame[1] = 0xAA
    if fault == "ack": struct.pack_into("<I", frame, 20, (2 << 16) | (1 << 20) | (1 << 21))
    if fault == "mode": struct.pack_into("<I", frame, 20, (1 << 16) | (1 << 21))
    if fault == "request": struct.pack_into("<I", frame, 16, 19)
    if fault == "json": frame[-1] = 0xFF
    if fault == "length": frame.pop()
    ticket = ReceiptTicket(1, 18, 0xB9) if fault == "ticket_kind" else TICKET
    assert match(bytes(frame), ticket, generation=2 if fault == "generation" else 1) is None


def test_negative_application_result_preserved_without_claiming_transport_receipt():
    frame = bytearray(_response(NODE, DEVICE, None))
    frame[32] = 0
    struct.pack_into("<H", frame, 36, 20001)
    reply = match(bytes(frame))
    assert reply.error_code == 20001 and reply.value is None


def test_oversized_payload_and_deep_json_fail_closed():
    assert match(_response(NODE, DEVICE, "x" * 4096)) is None
    raw = b"[" * 1500 + b"0" + b"]" * 1500
    frame = bytearray(_response(NODE, DEVICE, None)[:40] + raw)
    struct.pack_into("<H", frame, 2, len(frame))
    struct.pack_into("<H", frame, 38, len(raw))
    assert match(bytes(frame)) is None
