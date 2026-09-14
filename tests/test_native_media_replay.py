import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import build_kcp_push
from scripts import pcap_input
from scripts.replay_native_media import replay


def ipv4(wire):
    body = bytearray(28 + len(wire))
    body[0] = 0x45
    body[9] = 17
    struct.pack_into("!H", body, 2, len(body))
    body[12:20] = bytes((192, 0, 2, 1, 192, 0, 2, 2))
    struct.pack_into("!HHH", body, 20, 50000, 50001, len(wire) + 8)
    body[28:] = wire
    return bytes(body)


def write_pcap(path, records, *, endian="<", nano=False, link=101):
    magic = (b"\x4d\x3c\xb2\xa1" if nano else b"\xd4\xc3\xb2\xa1")
    if endian == ">":
        magic = magic[::-1]
    with path.open("wb") as output:
        output.write(magic + struct.pack(endian + "HHIIII", 2, 4, 0, 0, 65535, link))
        for seconds, fraction, body in records:
            output.write(struct.pack(endian + "IIII", seconds, fraction, len(body), len(body)))
            output.write(body)


@pytest.mark.parametrize("endian,nano", [("<", False), (">", False), ("<", True), (">", True)])
def test_streaming_pcap_timestamps(tmp_path, endian, nano):
    path = tmp_path / "sample.pcap"
    write_pcap(path, [(1, 500_000_000 if nano else 500_000, b"x")], endian=endian, nano=nano)
    assert list(pcap_input.packets(path)) == [(1.5, b"x")]


def test_replay_reorders_and_sanitizes(tmp_path):
    path = tmp_path / "sample.pcap"
    records = [(1, 0, ipv4(build_kcp_push(42, 1, b"\x04\x02\x04\x00"))),
               (1, 100, ipv4(build_kcp_push(42, 0, b"\x03\x00\x04\x00")))]
    write_pcap(path, records)
    result = replay(path)
    assert result["valid_mtp_datagrams"] == 2
    flow, = result["flows"]
    assert flow["first_sequence"] == 1 and flow["saw_sequence_zero"]
    assert flow["messages"] == 2 and flow["next_sequence"] == 2 and flow["error"] is None
    assert "192.0.2" not in json.dumps(result)
    assert "50000" not in json.dumps(result)


def test_missing_start_is_not_guessed_and_late_gap_is_reported(tmp_path):
    path = tmp_path / "sample.pcap"
    write_pcap(path, [(1, 0, ipv4(build_kcp_push(42, 1, b"x"))),
                      (4, 0, ipv4(build_kcp_push(42, 2, b"y"))),
                      (5, 0, ipv4(build_kcp_push(42, 0, b"z")))])
    flow, = replay(path)["flows"]
    assert flow["messages"] == 0 and flow["blocked_sequence"] == 0
    assert flow["error"] == "KCP assembly deadline exceeded"
    assert flow["blocked_sequence_seen_later"]


@pytest.mark.parametrize("kind", ["header", "link", "truncated", "oversize", "backwards"])
def test_reject_invalid_capture(tmp_path, kind):
    path = tmp_path / "sample.pcap"
    write_pcap(path, [(2, 0, b"x"), (1 if kind == "backwards" else 3, 0, b"y")],
               link=1 if kind == "link" else 101)
    if kind == "header":
        path.write_bytes(b"broken")
    elif kind == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    elif kind == "oversize":
        with path.open("r+b") as output:
            output.seek(24 + 8)
            output.write(struct.pack("<II", 2**30, 2**30))
    with pytest.raises(ValueError):
        list(pcap_input.packets(path))


def test_udp_refuses_ip_fragments_and_inconsistent_lengths():
    wire = ipv4(b"payload")
    assert pcap_input.udp(wire)[2] == b"payload"
    assert pcap_input.udp(wire[:-1]) is None
    fragmented = bytearray(wire)
    struct.pack_into("!H", fragmented, 6, 0x2000)
    assert pcap_input.udp(bytes(fragmented)) is None
    invalid = bytearray(wire)
    struct.pack_into("!H", invalid, 24, 7)
    assert pcap_input.udp(bytes(invalid)) is None
