"""Bounded streaming reader for classic RAW-IPv4 PCAPs; no packet payload logs."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

MAX_FILE = 64 * 1024 * 1024
MAX_PACKET = 65535
MAX_RECORDS = 100_000
MAGIC = {b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
         b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
         b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
         b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000)}


def packets(path: Path) -> Iterator[tuple[float, bytes]]:
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError("PCAP must be a regular file of at most 64 MiB")
    with path.open("rb") as source:
        header = source.read(24)
        if len(header) != 24 or header[:4] not in MAGIC:
            raise ValueError("unsupported or truncated classic PCAP header")
        endian, precision = MAGIC[header[:4]]
        major, minor, _zone, _sigfigs, snaplen, link = struct.unpack(endian + "HHIIII", header[4:])
        if (major, minor) != (2, 4) or link not in (101, 228) or not 1 <= snaplen <= MAX_PACKET:
            raise ValueError("expected bounded RAW IPv4 PCAP 2.4")
        consumed, records = 24, 0
        previous = 0.0
        while True:
            record = source.read(16)
            if not record:
                return
            if len(record) != 16:
                raise ValueError("truncated PCAP record")
            seconds, fraction, captured, original = struct.unpack(endian + "IIII", record)
            records += 1
            consumed += 16 + captured
            if (captured > snaplen or captured > original or fraction >= precision
                    or records > MAX_RECORDS or consumed > MAX_FILE):
                raise ValueError("PCAP record budget or length invalid")
            body = source.read(captured)
            if len(body) != captured:
                raise ValueError("truncated PCAP packet")
            timestamp = seconds + fraction / precision
            if timestamp < previous:
                raise ValueError("PCAP timestamps moved backwards")
            previous = timestamp
            yield timestamp, body


def udp(packet: bytes) -> tuple[tuple[str, int], tuple[str, int], bytes] | None:
    if len(packet) < 20 or packet[0] >> 4 != 4 or packet[9] != 17:
        return None
    ihl = (packet[0] & 15) * 4
    total, fragment = struct.unpack_from("!H", packet, 2)[0], struct.unpack_from("!H", packet, 6)[0]
    # No IP fragment reassembly or incomplete/truncated packet inference here.
    if ihl < 20 or total > len(packet) or total < ihl + 8 or fragment & 0x3FFF:
        return None
    sport, dport, length = struct.unpack_from("!HHH", packet, ihl)
    if length < 8 or ihl + length != total:
        return None
    source = (".".join(str(n) for n in packet[12:16]), sport)
    destination = (".".join(str(n) for n in packet[16:20]), dport)
    return source, destination, packet[ihl + 8:total]
