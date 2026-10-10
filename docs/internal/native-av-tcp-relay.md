# Native AV over TCP relay — 2026-10-10

## Live result

One bounded camera-3 experiment completed native relay measurement and the
INIT/ACCEPT/START AV lifecycle without the vendor app or emulator. The TCP owner
parsed **one encoding header, four video records and five audio records**. It
then sent CLOSE and received its exact KCP transport acknowledgement.

This is transport/AV-parser interoperability, **not decoded-image validation**,
maximum-resolution proof, a dashboard feature or LAN-only operation. The relay
still belongs to the vendor infrastructure. No decoded picture was displayed;
no media was retained, written to disk or sent to a speaker.

Sanitized observation:

- Current measurement sequence/timestamp/link/source/destination matched.
- `ready=true` requires INIT receipt, correlated ACCEPT, peer START, our START
  receipt and parsed encoding; a meter ACK alone does not satisfy it.
- 21,605 bytes and 48 records received; 2,828 bytes emitted by the AV owner.
- INIT: two transmission attempts; START: two; CLOSE: three. These are bounded,
  byte-identical retries of their respective control sequence, not new sessions.
- TCP reads ranged from 38 to 4096 bytes; split/coalesced frames were handled by
  the connection-local framer. No datagram-to-record-size assumption was used.
- `close_acknowledged=true`, protocol owner closed, TCP socket closed in `finally`.
  CLOSE receipt means transport receipt, not semantic camera teardown. Broker B9
  remote receipt remains **unconfirmed**; the outer route owner attempted cleanup.
- Process: 9.508 seconds, peak 45.8 MiB, zero swap under a 192 MiB/no-swap,
  50%-CPU/30-second cgroup. No container rebuild/restart or other camera access.

The ignored runner is `re/mtp_tcp_av_once.py`, invoked only with explicit
`CCG_AV_TCP=1` inside the reviewed camera-3 relay diagnostic. It emits metadata
only. The request-only legacy counters in the enclosing diagnostic were adjusted
after this test so they no longer falsely report zero TCP bytes/no meter alongside
the nested AV result. This bookkeeping change was not followed by another test.

## Reusable composition

`p2p/mtp_tcp_av.py` adds `TcpAvSession`, separate from socket discovery, relay
selection and production registration. It composes the already-tested framer,
measurement matcher, TCP KCP adapter and `AvHandshake` on one exclusive connection.

The owner emits exactly one periodic measurement before AV. Only its matching
receipt creates the AV lifecycle. All coalesced KCP segments must belong to the
current base or high-bit conversation. Meter requests get correlated replies on
the same connection; unmatched/duplicate meter ACKs do not reset the lifecycle.
Partial/failed sends must terminate the owner; returned wires count as attempted.

Local diagnostic policy: three seconds for the measurement, fifteen seconds
total including closing, 2 MiB received, 256 KiB sent and 4096 incoming records.
The framer additionally caps reads and batches. EOF/error/cancellation releases
partial bytes and cryptographic cookie material; the instance cannot reconnect.
The socket caller still owns physical socket closure and broker teardown. Runtime
integration must acquire the existing application camera-operation reservation.

386 focused tests passed in 2.63 seconds, peak 87 MiB/no swap. Tests cover the
complete lifecycle, fragmented/coalesced input, maintenance responses, stale/
unsent/duplicate receipts, wrong peer/conversation, premature CLOSE, traffic/time
budgets, partial-send cancellation and EOF. Ruff and targeted Mypy passed.

## Next steps

1. Reuse the existing bounded video sample/decoder diagnostic to prove that these
   TCP-delivered video records decode. Do not infer resolution or codecs from
   record counts; report encoding metadata explicitly in that next observation.
2. Resolve remaining broker teardown uncertainty and validate sustained reception,
   backpressure and reconnect ownership before production use.
3. Preserve maximum-resolution preference and the generic single-producer driver
   source contract, local fan-out and RTSP fallback. Do not add a second persistent
   camera connection or advertise native-video capability from this short test.

See [SDK TCP mapping](yoosee-mtp-channel-selection.md),
[relay measurement](yoosee-push-live-observation.md),
[earlier direct-route decode](native-av-first-live-decode.md).
