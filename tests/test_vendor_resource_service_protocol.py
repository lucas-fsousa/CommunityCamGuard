from __future__ import annotations

import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.alarm_voice import build_alarm_voice_query
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.resource_service_protocol import (
    FragmentReassembler,
    build_alarm_voice_catalog_request,
    build_fragment_ack,
    compressed_gute_payload_length,
    decode_fragment_packet,
    encode_fragment_header,
    parse_alarm_voice_catalog_response,
)

NODE = CertifiedNode(("192.0.2.10", 19800), 0x1020304050607080, bytes(range(32)), 7)


def test_catalog_request_has_fixed_read_only_route_and_shape():
    request = build_alarm_voice_catalog_request(
        NODE,
        build_alarm_voice_query(system=True, language="pt-BR", access_id=123),
        0x11223344,
    )
    plain = gute_mode2_decrypt(request, NODE.session_key)

    assert plain[:2] == b"\x7e\xc0"
    assert plain[0x20:0x2E] == b"HTTP_PROXY/REQ"
    payload_offset = 0x20 + plain[0x1D] + 1
    payload_length = struct.unpack_from("<H", plain, 0x1E)[0]
    payload = json.loads(plain[payload_offset : payload_offset + payload_length])
    assert payload["http"] == {"url": "/resfile/queryres", "type": "POST"}
    assert payload["data"]["resTypes"] == [4]
    assert payload["data"]["keyWord"] == "language_8"


@pytest.mark.parametrize(
    "query",
    [
        {"pageSize": 20, "curPage": 0, "resTypes": [4], "bySys": 0, "accessId": "1", "write": 1},
        {"pageSize": 100, "curPage": 0, "resTypes": [4], "bySys": 0, "accessId": "1"},
        {"pageSize": 20, "curPage": 0, "resTypes": [6], "bySys": 0, "accessId": "1"},
    ],
)
def test_catalog_request_rejects_any_shape_outside_fixed_read_contract(query):
    with pytest.raises(ValueError):
        build_alarm_voice_catalog_request(NODE, query, 1)


def test_catalog_response_is_bounded_and_requires_c1():
    payload = b'{"code":0}'
    response = bytearray(0x20 + len(payload))
    response[:2] = b"\x7e\xc1"
    struct.pack_into("<I", response, 0x10, 0xAABBCCDD)
    response[0x18] = 1
    struct.pack_into("<H", response, 0x1C, 0)
    struct.pack_into("<H", response, 0x1E, len(payload))
    response[0x20:] = payload

    assert parse_alarm_voice_catalog_response(response) == (0xAABBCCDD, 0, payload)
    response[1] = 0xB5
    assert parse_alarm_voice_catalog_response(response) is None


def _fragment(index: int, total: int, original: bytes, chunk_size: int) -> bytes:
    payload = original[index * chunk_size : (index + 1) * chunk_size]
    frame = bytearray(0x18 + len(payload))
    frame[:2] = b"\x70\x01"
    struct.pack_into("<H", frame, 2, len(frame))
    struct.pack_into("<Q", frame, 4, NODE.session_id)
    struct.pack_into("<I", frame, 0x0C, 0x55667788)
    struct.pack_into("<H", frame, 0x10, len(original))
    frame[0x12] = total
    frame[0x13] = index
    frame[0x18:] = payload
    return encode_fragment_header(frame, mask=0x1200 + index)


def test_fragments_reassemble_out_of_order_and_generate_correlated_ack():
    original = bytes(range(256)) * 4
    chunk_size = 300
    total = (len(original) + chunk_size - 1) // chunk_size
    packets = [
        decode_fragment_packet(_fragment(i, total, original, chunk_size)) for i in range(total)
    ]
    reassembler = FragmentReassembler()

    assert reassembler.add(packets[-1]) is None
    for packet in packets[1:-1]:
        assert reassembler.add(packet) is None
    assert reassembler.add(packets[0]) == original

    ack = bytearray(build_fragment_ack(packets[0], mask=0x4321))
    assert ack[:4] == b"\x70\x02\x18\x00"
    decoded_ack = bytearray(ack)
    mask = struct.unpack_from("<H", decoded_ack, 0x14)[0]
    for offset in range(4, 0x14, 2):
        struct.pack_into(
            "<H",
            decoded_ack,
            offset,
            struct.unpack_from("<H", decoded_ack, offset)[0] ^ mask,
        )
    assert decoded_ack[4:0x14] == packets[0].decoded_header[4:0x14]


def test_fragment_checksum_and_compression_marker_fail_closed():
    wire = bytearray(_fragment(0, 1, b"payload", 20))
    wire[0x16] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        decode_fragment_packet(wire)

    plain = bytearray(0x20)
    struct.pack_into("<I", plain, 0x14, (2 << 16) | 1 | (1234 << 1))
    assert compressed_gute_payload_length(plain) == 1234
    struct.pack_into("<I", plain, 0x14, 2 << 16)
    assert compressed_gute_payload_length(plain) is None
