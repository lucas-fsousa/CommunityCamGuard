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

## TCP video decode follow-up

The next acquisition failed during access-session establishment, before A4 or
any AV START. It allocated no camera media route and cleared the empty sample.
A separate read-only camera-3 access check then succeeded without sending A4;
there is no evidence from these results that the enrollment token had expired.

One subsequent acquisition after that check completed the lifecycle and the
existing sequential `decode_sample` validation **after TCP/UDP local closure and
the B9 cleanup attempt**:

- HEVC, **640×360**, nine frames decoded by ffprobe and strict ffmpeg null-output
  decode, agreeing with the parsed encoding header and retained frame count.
- 7,144 video bytes retained temporarily in RAM; zero pre-IDR discards; raw
  timestamp span 800,000 ticks. No frame rate or time scale inferred here.
- One encoding header, nine video and thirteen audio records parsed. Audio was
  counted only, not retained, decoded or played.
- 22,472 TCP bytes/53 records received; 3,136 bytes emitted. Two attempts each
  for INIT, START and CLOSE, preserving each control's sequence/timestamp.
- CLOSE transport receipt confirmed, owner and sockets closed, sample cleared.
  Broker B9 remote receipt still unconfirmed. No media file/image was produced.
- Combined parent/decoder cgroup peak **114.8 MiB**, zero swap, 10.072 seconds;
  cap 256 MiB/no swap/50% CPU/60 seconds. Sequential decoders use one thread and
  the existing per-child address-space/CPU/wall-time limits.

This upgrades TCP evidence from parsed records to actual decodability. It still
does not prove maximum resolution, sustained playback, visual correctness or
LAN-only operation. Production and the dashboard remain unchanged.

CI follow-up: the full import-aware type check required an explicit `V1Record`
list annotation in the new owner; corrected it and verified locally with normal
Mypy imports (77 MiB/no swap). A skip-import targeted check had missed that issue.

## HD startup follow-up — 2026-10-11

One camera-3 acquisition used the [SDK 6.45 preconnection preparation](native-video-definition.md)
with uniform HD enum 3 in both A4 and INIT. No platform was guessed and no
mid-stream quality command was sent. The camera advertised codec 5 at
**1920×1080**; strict sequential ffprobe/ffmpeg validation decoded eight HEVC
frames at those dimensions after local socket closure and the B9 cleanup attempt.

- 28,460 video bytes temporarily in RAM; zero pre-IDR discards; raw timestamp
  span 700,000 ticks. Twelve audio records counted only, never played or retained.
- 64,729 TCP bytes/91 records received, 4,474 bytes emitted by the AV owner.
- INIT, START and CLOSE each needed two byte-identical transmission attempts.
  Readiness and CLOSE transport receipt confirmed; owner/socket closed and sample
  cleared. Broker B9 receipt remains unconfirmed, not silently treated as success.
- Combined parent/decoder peak **140.6 MiB**, zero swap, **9.752 seconds**;
  256 MiB/no-swap/50%-CPU/60-second cap, no production restart or other camera use.

HD is now decoded evidence for this unit. It is not sensor-maximum verification,
visual inspection, sustained reception, LAN-only access or a production feature.
The earlier 640×360 observation above remains the baseline, not the desired default.

## Next steps

1. Preserve the proven 1920×1080 HD startup path; verify sensor maximum separately
   rather than assuming the enum name guarantees it on every model.
2. Resolve remaining broker teardown uncertainty and validate sustained reception,
   backpressure and reconnect ownership before production use.
3. Preserve maximum-resolution preference and the generic single-producer driver
   source contract, local fan-out and RTSP fallback. Do not add a second persistent
   camera connection or advertise native-video capability from this short test.

See [SDK TCP mapping](yoosee-mtp-channel-selection.md),
[relay measurement](yoosee-push-live-observation.md),
[earlier direct-route decode](native-av-first-live-decode.md).
