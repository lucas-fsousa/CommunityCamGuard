"""Socket-free native PTZ boundary; receipt correlation is not motor-state proof."""
import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.ptz_protocol import (
    DIRECTIONS,
    PtzReply,
    build_ptz_request,
    direction_supported,
    parse_ptz_reply,
)
from backend.app.drivers.yoosee.p2p.wire import new_header

NODE = CertifiedNode(("192.0.2.1", 19800), 9, bytes(range(32)), 17)
ARGS = dict(node=NODE, access_id=123, device_id=456, sequence=18, message_id=44, request_id=55)


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("pressed", [False, True])
def test_only_fixed_press_release_payload_is_encoded(direction, pressed):
    wire = build_ptz_request(**ARGS, direction=direction, pressed=pressed)
    frame = gute_mode2_decrypt(wire, NODE.session_key)
    assert struct.unpack_from("<QQ", frame, 0x1C) == (456, 123)
    length = struct.unpack_from("<H", frame, 0x30)[0]
    payload = frame[0x34:0x34 + length]
    assert payload[:8] == b"\x01\xff\x00\x00" + struct.pack("<I", 55)
    assert json.loads(payload[8:]) == {"type": 2, "data": {
        "dir": DIRECTIONS[direction], "touchType": 0 if pressed else 1,
    }}


@pytest.mark.parametrize("direction,pressed", [("diagonal", True), ("left", 1), ("right", "false")])
def test_invalid_motion_is_rejected(direction, pressed):
    with pytest.raises(ValueError):
        build_ptz_request(**ARGS, direction=direction, pressed=pressed)


def reply(*, kind=0xB9, session=9, mode=2, ack=False, sequence=18, source=456,
          destination=123, message=44, request=55, value=None):
    payload = b"\x01\x00\x00\x00" + struct.pack("<I", request) + json.dumps(
        {"type": 2, "err": 0} if value is None else value,
    ).encode()
    frame = new_header(kind, 0x34 + len(payload), session, sequence, (mode << 16) | (int(ack) << 20))
    frame[0] = 0x7E
    struct.pack_into("<QQIH", frame, 0x1C, destination, source, message, len(payload))
    frame[0x34:] = payload
    return bytes(frame)


@pytest.mark.parametrize("changes", [
    {"session": 10}, {"mode": 0}, {"source": 457}, {"destination": 124},
    {"request": 56}, {"kind": 0xAA}, {"value": {"type": 2, "err": False}},
    {"value": {"type": 2.0, "err": 0}}, {"value": {"type": 2}},
])
def test_unrelated_or_ambiguous_reply_is_rejected(changes):
    assert parse_ptz_reply(reply(**changes), **ARGS) is None


def test_delivery_and_application_results_remain_distinct():
    assert parse_ptz_reply(reply(ack=True), **ARGS) == PtzReply(transport_receipt=True)
    assert parse_ptz_reply(reply(ack=True, sequence=19), **ARGS) is None
    assert parse_ptz_reply(reply(kind=0xBA), **ARGS) == PtzReply(peer_receipt=True)
    assert parse_ptz_reply(reply(kind=0xBA, message=45), **ARGS) is None
    assert parse_ptz_reply(reply(message=99), **ARGS) == PtzReply(error_code=0)
    assert parse_ptz_reply(reply(value={"type": 2, "err": 7}), **ARGS) == PtzReply(error_code=7)
    assert parse_ptz_reply(b"", **ARGS) is None
    assert parse_ptz_reply(reply()[:0x35], **ARGS) is None
    assert parse_ptz_reply(reply() + bytes(4096), **ARGS) is None


@pytest.mark.parametrize("status,directions", [(7, set(DIRECTIONS)), (3, {"left", "right"}),
                                               (5, {"up", "down"}), (6, set()), (0, set())])
def test_axis_bits_do_not_grant_another_axis(status, directions):
    root = {"t": 123, "stVal": {"ptzInfo": {"id0_status": status}}}
    assert {direction for direction in DIRECTIONS if direction_supported(root, direction)} == directions


@pytest.mark.parametrize("value", [None, {"id0_status": 7}, {"t": 0, "stVal": {"ptzInfo": {"id0_status": 7}}},
    {"t": 1, "stVal": {"ptzInfo": {"id0_status": True}}},
    {"t": 1, "stVal": {"ptzInfo": {"id1_status": 7}}},
    {"t": -1, "stVal": {"ptzInfo": {"id0_status": 7}}}])
def test_missing_stale_or_other_head_evidence_never_grants_movement(value):
    assert not direction_supported(value, "left")
