"""Content-free, bounded PCAP triage. Candidates are NOT authenticated messages.

TCP inspection is segment-boundary only: no reassembly, decryption or conclusions
from absence. Invoke as python -m scripts.inspect_push_capture FILE.
"""

import argparse
import json
import struct
from collections import Counter
from pathlib import Path

from scripts.pcap_input import packets, udp

PUSH_TYPES = frozenset((4, 5, 6, 7, 8, 11, 13))


def tcp_payload(packet: bytes) -> bytes | None:
    if len(packet) < 20 or packet[0] >> 4 != 4 or packet[9] != 6:
        return None
    ihl = (packet[0] & 15) * 4
    total, fragment = struct.unpack_from("!H", packet, 2)[0], struct.unpack_from("!H", packet, 6)[0]
    if ihl < 20 or total > len(packet) or total < ihl + 20 or fragment & 0x3FFF:
        return None
    offset = (packet[ihl + 12] >> 4) * 4
    if offset < 20 or ihl + offset > total:
        return None
    return packet[ihl + offset:total]


def classify(payload: bytes, *, tcp: bool) -> str | None:
    """Check one header at payload start; never scan arbitrary bytes for magic."""
    if len(payload) < 20 or payload[0] != 3 or payload[1] not in PUSH_TYPES:
        return None
    total = 20 + struct.unpack_from("<H", payload, 4)[0]
    if not 20 < total <= 0x8400:
        return None
    kind = f"type_{payload[1]:02x}"
    if tcp:
        suffix = "complete_prefix" if total <= len(payload) else "partial_prefix"
    else:
        if total != len(payload):
            return None
        suffix = "exact_datagram"
    return kind + "_" + suffix


def inspect(path: Path) -> dict:
    counts: Counter[str] = Counter()
    for _timestamp, packet in packets(path):
        counts["records"] += 1
        datagram = udp(packet)
        if datagram is not None:
            payload = datagram[2]
            transport = "udp"
        else:
            payload = tcp_payload(packet)
            if payload is None:
                counts["unhandled_packets"] += 1
                continue
            transport = "tcp"
        counts[transport + "_packets"] += 1
        if payload:
            counts[transport + "_payload_packets"] += 1
        candidate = classify(payload, tcp=transport == "tcp")
        if candidate is not None:
            counts[transport + "_" + candidate] += 1
    return {"scope": "raw_ipv4_segment_boundaries_only",
            "authenticated": False, "tcp_reassembled": False,
            "counts": dict(sorted(counts.items()))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.capture)
    except (ValueError, OSError):
        parser.exit(2, "Capture rejected: unsupported, unreadable or outside safety bounds.\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
