"""Synthetic capture classifier tests; never decode or log credentials."""

import struct

import pytest

from scripts.inspect_push_capture import classify, inspect, tcp_payload


def frame(kind=7, body=b"abcd"):
    header = bytearray(20)
    header[:2] = bytes((3, kind))
    struct.pack_into("<H", header, 4, len(body))
    return bytes(header) + body


def ip_tcp(payload):
    packet = bytearray(40)
    packet[0], packet[9], packet[32] = 0x45, 6, 0x50
    struct.pack_into("!H", packet, 2, 40 + len(payload))
    return bytes(packet) + payload


def test_exact_udp_and_coalesced_tcp():
    assert classify(frame(), tcp=False) == "type_07_exact_datagram"
    assert classify(frame() + frame(), tcp=False) is None
    assert classify(frame() + frame(), tcp=True) == "type_07_complete_prefix"
    assert classify(frame()[:-1], tcp=True) == "type_07_partial_prefix"
    assert classify(frame()[:-1], tcp=False) is None


@pytest.mark.parametrize("payload", [b"", frame()[:19], b"prefix" + frame(),
                                     frame(255), frame(body=b"")])
def test_does_not_invent_candidates(payload):
    assert classify(payload, tcp=True) is None


def test_tcp_bounds_and_fragments():
    packet = ip_tcp(frame())
    assert tcp_payload(packet) == frame()
    assert tcp_payload(packet[:-1]) is None
    for offset, value in ((0, 0x44), (9, 17), (6, 0x20), (32, 0x40), (32, 0xF0)):
        changed = bytearray(packet)
        changed[offset] = value
        assert tcp_payload(bytes(changed)) is None


def test_report_never_contains_payload(tmp_path):
    payload = frame(body=b"synthetic-secret")
    packet = ip_tcp(payload)
    header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101)
    record = struct.pack("<IIII", 1, 0, len(packet), len(packet))
    path = tmp_path / "test.pcap"
    path.write_bytes(header + record + packet)
    report = inspect(path)
    assert report["counts"] == {"records": 1, "tcp_packets": 1,
                                "tcp_payload_packets": 1, "tcp_type_07_complete_prefix": 1}
    assert report["authenticated"] is False
    assert "secret" not in str(report)
