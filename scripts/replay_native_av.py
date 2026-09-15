"""Offline, bounded two-conversation AV replay; counts only, no socket or media files."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from backend.app.drivers.yoosee.p2p.av_receive import AvReceiver
from backend.app.drivers.yoosee.p2p.media_protocol import KCP_PUSH, parse_kcp_segments

from .captured_media import CapturedSessions
from .pcap_input import packets, udp


def audit(path: Path, control_flow: str = "flow4", *, delay_accept: bool = False) -> dict:
    sessions = CapturedSessions()
    flows: dict[tuple, str] = {}
    receiver = None
    route = binding = None
    start = 0.0
    clock = [0.0]
    delayed: bytes | None = None
    injected = False
    peak_buffered = 0
    counts = dict(datagrams=0, headers=0, video_frames=0, audio_frames=0, unhandled_commands=0)
    try:
        for now, raw in packets(path):
            if receiver is not None:
                clock[0] = now - start
                if clock[0] >= 10:
                    break  # Deliberate bounded prefix, not a complete-file claim.
                receiver.poll()
            parsed = udp(raw)
            if parsed is None:
                continue
            peer, destination, wire = parsed
            sessions.observe(peer, destination, wire)
            if wire[:2] != b"\xc0\x10":
                continue
            try:
                segments = parse_kcp_segments(wire)
            except ValueError:
                continue
            for conv in sorted({s.conv for s in segments if s.command == KCP_PUSH}):
                key = (peer, destination, conv)
                if key not in flows:
                    if len(flows) >= 16:
                        raise ValueError("flow budget exceeded")
                    flows[key] = f"flow{len(flows) + 1}"
                if receiver is None and flows[key] == control_flow:
                    binding = sessions.lookup(peer, destination, conv)
                    if binding is None or conv >> 31 != 1:
                        raise ValueError("missing correlated control channel")
                    route = (peer, destination, conv & 0x7FFFFFFF)
                    start = now
                    receiver = AvReceiver(peer, route[2], binding.call_id, binding.cookie,
                                          control_conv=conv, clock=lambda: clock[0])
            if receiver is None or route is None:
                continue
            if peer != route[0] or destination != route[1]:
                continue
            if sessions.lookup(peer, destination, route[2]) != binding:
                raise ValueError("capture binding changed")
            if delay_accept and not injected:
                if not any(s.conv == route[2] | 0x80000000 and s.command == KCP_PUSH
                           and s.fragment == 0 and len(s.body) == 76 and s.body[:4] == b"\x03\x00\x4c\x00"
                           and struct.unpack_from("<I", s.body, 8)[0] == 2 for s in segments):
                    raise ValueError("scenario requires first complete ACCEPT datagram")
                delayed, injected = wire, True
                continue
            deliveries = [wire]
            if delayed is not None and clock[0] >= 0.1:
                deliveries.insert(0, delayed)
                delayed = None
            for delivery in deliveries:
                result = receiver.receive(delivery, peer)
                peak_buffered = max(peak_buffered, receiver.buffered_bytes)
                counts["datagrams"] += 1
                counts["unhandled_commands"] += result.unhandled_commands
                for record in result.records:
                    counts["headers"] += record.encoding is not None
                    counts["video_frames"] += bool(record.video)
                    counts["audio_frames"] += len(record.audio)
        if delayed is not None or receiver is None or receiver.phase != "active" or not counts["video_frames"]:
            raise ValueError("capture did not reach active video")
        return dict(control_flow=control_flow, phase=receiver.phase, **counts,
                    buffered_bytes=receiver.buffered_bytes, prefix_limit_seconds=10,
                    delayed_accept=injected, peak_buffered_bytes=peak_buffered)
    finally:
        if receiver is not None:
            receiver.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--control-flow", default="flow4")
    parser.add_argument("--delay-accept", action="store_true", help="delay first ACCEPT by at least 100 ms offline")
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.capture, args.control_flow, delay_accept=args.delay_accept), indent=2))
    except (OSError, ValueError):
        raise SystemExit("AV replay rejected") from None


if __name__ == "__main__":
    main()
