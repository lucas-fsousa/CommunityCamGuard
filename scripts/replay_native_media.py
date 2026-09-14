"""Offline sanitized MTP replay: python -m scripts.replay_native_media FILE.pcap.

No network or file writes, no payload/credential/address output. Per-direction
conversations stay independent. Missing sequence zero is reported, never guessed.
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from pathlib import Path

from backend.app.drivers.yoosee.p2p.kcp_receive import KcpReceiver, ReceiveError
from backend.app.drivers.yoosee.p2p.media_protocol import KCP_PUSH, parse_kcp_segments
from backend.app.drivers.yoosee.p2p.media_receive import MediaReceiver

from .pcap_input import packets, udp

MAX_FLOWS = 16


class Flow:
    def __init__(self, label: str, peer: tuple[str, int], conv: int, now: float) -> None:
        self.now = now
        self.receiver = MediaReceiver(peer, KcpReceiver(conv, clock=lambda: self.now))
        self.report: dict = dict(
            flow=label, pushes=0, messages=0, message_bytes=0, fragments=0,
            max_fragment=0, peak_buffer_bytes=0, saw_sequence_zero=False,
            first_sequence=None, gap_observations=0, error=None,
            blocked_sequence=None, blocked_sequence_seen_later=False,
        )
        self.types: Counter[int] = Counter()
        self.length_mismatches = 0

    def consume(self, now: float, wire: bytes, peer: tuple[str, int]) -> None:
        self.now = now
        segments = parse_kcp_segments(wire)
        for segment in segments:
            if segment.conv != self.receiver.receiver.conv or segment.command != KCP_PUSH:
                continue
            self.report["pushes"] += 1
            self.report["saw_sequence_zero"] |= segment.sequence == 0
            if self.report["first_sequence"] is None:
                self.report["first_sequence"] = segment.sequence
            self.report["fragments"] += int(segment.fragment != 0)
            self.report["max_fragment"] = max(self.report["max_fragment"], segment.fragment)
            if self.report["error"] and segment.sequence == self.report["blocked_sequence"]:
                self.report["blocked_sequence_seen_later"] = True
        if self.report["error"]:
            return
        try:
            result = self.receiver.receive(wire, peer)
        except ReceiveError as exc:
            self.report["error"] = str(exc)
            self.report["blocked_sequence"] = self.receiver.receiver.next_sequence
            return
        buffered = self.receiver.receiver.buffered_bytes
        self.report["peak_buffer_bytes"] = max(self.report["peak_buffer_bytes"], buffered)
        self.report["gap_observations"] += int(bool(buffered))
        for message in result.messages:
            self.report["messages"] += 1
            self.report["message_bytes"] += len(message)
            if len(message) >= 4 and struct.unpack_from("<H", message, 2)[0] == len(message):
                self.types[message[0]] += 1
            else:
                self.length_mismatches += 1

    def finish(self) -> dict:
        self.report["buffered_at_capture_end"] = self.receiver.receiver.buffered_bytes
        self.report["next_sequence"] = self.receiver.receiver.next_sequence
        self.report["tlv_types"] = dict(sorted(self.types.items()))
        self.report["tlv_length_mismatches"] = self.length_mismatches
        self.receiver.receiver.close()
        return self.report


def replay(path: Path) -> dict:
    flows: dict[tuple, Flow] = {}
    records = valid = malformed = 0
    for now, raw in packets(path):
        records += 1
        parsed = udp(raw)
        if parsed is None:
            continue
        peer, destination, wire = parsed
        if wire[:2] != b"\xc0\x10":
            continue
        try:
            segments = parse_kcp_segments(wire)
        except ValueError:
            malformed += 1
            continue
        valid += 1
        conversations = {segment.conv for segment in segments if segment.command == KCP_PUSH}
        for conv in sorted(conversations):
            key = (peer, destination, conv)
            if key not in flows:
                if len(flows) >= MAX_FLOWS:
                    raise ValueError("PCAP exceeds 16 directional KCP flows")
                flows[key] = Flow(f"flow{len(flows)+1}", peer, conv, now)
            flows[key].consume(now, wire, peer)
    return dict(records=records, valid_mtp_datagrams=valid, malformed_mtp_datagrams=malformed,
                flows=[flow.finish() for flow in flows.values()])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(replay(args.capture), indent=2))
    except (OSError, ValueError) as exc:
        # Do not echo a sensitive user path or bytes from the packet.
        raise SystemExit(f"Replay rejected: {type(exc).__name__}") from None


if __name__ == "__main__":
    main()
