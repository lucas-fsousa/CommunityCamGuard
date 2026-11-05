# Native transport investigation — 2026-11-05

Scope: camera3 only, no sound/light/motor/reset commands. Keep WSL safe through
small targeted tests/builds with explicit CPU/memory caps. Existing user changes
to recordings-host configuration remain outside these commits.

## Observed before changes

The real rotating retry history contains local decoder exit/progress failures,
TCP I/O/session timeouts and KCP assembly timeouts. Successful control/siren
acceptance does not establish continuous stream stability. Camera3's old LAN
address was separately corrected by MAC; that does not prove a P2P transport cause.

The decoder command overrides error logging with `-v quiet`, and an exited child
is reported before stderr is read. Consequently `DecoderExitedError` discards
both the exit code and the useful failure category. KCP assembly timeout similarly
closes/clears the receive buffer before retaining missing-sequence information.

## Diagnostic correction

FFmpeg retains error-level output on its existing nonblocking progress pipe.
No raw stderr, paths, URLs or media bytes are logged. Normal progress lines and
a bounded exit tail are classified into fixed labels; only a bounded return code
and that label reach the existing rotating native event log. `-xerror`, strict
bitstream validation, process memory/thread limits and MP4 budgets are unchanged.

KCP timeout preserves six numeric counters BEFORE clearing the receiver: expected
sequence, queued segments, assembled parts, first gap, remaining fragments and
buffered bytes. Only range-checked counters enter the event log. Assembly deadline,
window/byte limits, acknowledgment semantics and terminal close remain unchanged.
These facts distinguish waiting on a missing segment from an incomplete message
without packet payload recording or another camera connection.

281 targeted tests passed under192MiB/no swap/50% CPU, peak107.1MiB. Ruff and
Mypy402 passed. Deployment/real failure observation follows; no root cause or
fixed continuous stream is claimed from instrumentation alone.

## Live observation after deployment

Build `b-51704da39ac8` repeatedly retired camera3 with `mtp_record_length`,
interspersed with TCP handshake/control timeouts. Both GitHub CI and Image workflows
passed for `fe987f0`. The decoder has not supplied an exit category in these
pre-video attempts; increasing decoder timeouts would not address this evidence.

Length rejection now retains only the numeric MTP kind and its 11-bit declared
length, forwarded through the same bounded event channel. It still closes the
connection and keeps the SDK-derived 1500-byte ceiling and checksum requirement.
No packet payload is retained and no validation is relaxed. The actual offending
length must be observed before changing framing behavior.

The next run captured KCP gaps at sequences 620 and 1883, each with gap 1 and
97/102 queued segments (under 96 KiB), not a full receive buffer. A browser probe
received the initialization and one media fragment before retirement. FFmpeg
also exited with code 234 and an unclassified message. Fixed classification now
recognizes HEVC/MPEG-TS error-component tags and missing codec/muxer headers; raw
messages are still neither retained nor returned. This is further evidence
collection, not a demonstrated correction of either transport or decode failure.

## Latest deployment and passive acceptance

`86596c7` / image `3fb9837e0fbf` / build `b-29cb46445d20` resumed camera3
native/active revision126. CI42067495782 passed5930tests/1skip, Mypy402, frontend
contracts and Ruff; Image42067503181 passed. No sound/light/motor/reset command.

A90-second read-and-discard browser-protocol client received183binary messages
(69374913bytes), max gap3.05s. A subsequent300-second observation received598
(227445585bytes), max gap3.24s, without disconnect. The ending `TimeoutError` is
the observer's explicit duration limit, not a server/session timeout. Payloads
were discarded immediately, not accumulated. Native recording chunks advanced;
the other two sources remained legacy/available. Container had no OOM/restart
loop, around224MiB during observation.

Two closed recording epochs (including one from a decoder-exit attempt) passed
bounded strict offline video decode. The latter had120video/123audio packets,
with no duplicate/backward DTS after archive-clock repair. This does not prove
that live input clocks or the entire file are free of errors. A35-second passive
UDP header count saw no mapped MTP packets; that short observation does not
exclude an alternate route at another time. No raw packets or stderr persisted.

The stream is currently delivering, but intermittent failure is unresolved:
diagnostic changes alone must not be described as its root-cause correction.
Further checks must use real recurrence evidence, not repeat physical control
acceptance or alter production cameras.

## Recurrence and implementation follow-up

Longer observation did reproduce failure: KCP gaps at16:57:13 and17:11:11,
followed by startup muxer failures (`muxer_setup`, exit234) at16:57:24 and
17:11:20. At17:20:50 the source stopped retrying: the existing safe retirement
log located the unknown error at `_message:119/append:34`, specifically
`AV cross-channel receive budget exceeded`. Broker release and both socket
closures were confirmed; there was no motion or siren activity to leave pending.

That pre-ACCEPT capacity/deadline condition is now explicitly classified as a
transient opening failure. Retry still requires confirmed outer route release,
physical socket closure and safe control cleanup. Existing256KiB/128-message/
one-second pending limits and byte validation stay intact. Unknown/malformed
protocol failures and authentication rejection remain non-retryable. This fixes
the permanent quarantine caused by treating a known opening-buffer limit as an
unknown protocol failure; it does not increase buffering or prove why ACCEPT was
delayed.

Two independent implementation corrections also follow the evidence:
[KCP window reports](native-kcp-window.md) and
[fair video/AAC stdin scheduling](native-av-fairness.md). The latter has a
deterministic partial-write/video-backlog regression; it is not inferred from
successful short media playback. Combined focused suite307passed, peak130.7MiB
under192MiB/no swap/50%CPU; Ruff and Mypy403 passed. Live acceptance of this
combined version follows deployment.

Deployment `f69dcca` / build `b-fde3e73e9103`, native/active129, restored delivery
without manual DB edits or camera actions. CI42093614624 passed5969tests/1skip;
Image42093614554 passed. Read-only MP4 inspection confirmed H264 + AAC initialization
and media fragments for both track IDs1/2. An observation disconnected after26.8s
(27messages/6059884bytes), with a fresh KCP gap1 at sequence1120,42queued segments,
10176bytes; safe retry/cleanup occurred. This disproves continuous stability of
the combined version despite the delivered corrections.

Further receive-window corrections now align native control advertisements with
the actual128-slot receiver and reserve an existing slot for a missing head.
They follow static/code inconsistency and a deterministic pressure regression,
not a claim that the observed small42-segment queue was full. Numeric rejection
counters will test whether a future missing sequence was previously outside our
advertised/retained window. See the updated KCP note. No timeout increase or
buffer expansion is used.

`2ae81fa` / `b-4ebad3f298f3`, native/active132, passed CI5977tests/1skip and
Image. Live observation still disconnected after103.3s (218messages/82632269bytes),
this time with a local `TimeoutError`. A later KCP gap at sequence196 had96queued
segments/77675bytes and **zero local window rejections**. Thus the receive-credit
corrections are not sufficient to fix the measured transport interruption.

A temporary read-only process observer samples only the native FFmpeg's state,
wait channel, CPU ticks, RSS and I/O counters; no arguments, media or credentials
are retained. One retirement followed an input-counter plateau with negligible
CPU use, not memory pressure or disk-blocked state. The container still had three
FFmpeg processes total (two legacy recording owners, one native A/V), no OOM or
restart loop. This observation does not identify every local timeout's stage.

## Controlled repair-deadline experiment

The continuous stream already has a5-second absolute complete-message idle
budget, but its KCP assembler inherited the finite experiment's2-second cutoff.
The missing segment above was not locally rejected and future packets continued
to arrive. Test whether the peer repairs it after2s but before the existing idle
budget ends: continuous assembly now uses that idle budget; finite diagnostics
and the raw receiver default remain2s. This DOES extend that continuous assembly
cutoff2→5s, not the session's existing overall idle limit. Memory/sequence/byte
validation and pre-ACCEPT/control deadlines remain unchanged.

A fixed numeric log records a complete assembly repaired after2s. Tests verify
repair at2.5s, unchanged closure at5s and no extension from duplicate traffic.
Production acceptance is experimental: retain only if actual delayed repairs/
continuous delivery justify it; do not describe the prior failures as resolved
merely because a larger wait was allowed.

`fb72955` / `b-f2b2e3054e8e`, active135, passed CI5980tests/1skip and Image.
The live experiment DID observe completed KCP repairs at2761,3473and3368ms,
all beyond the old2000ms cutoff and within the unchanged5000ms idle budget.
This is direct evidence for retaining the continuous repair-budget alignment.
It did not fix every failure: repeated `tcp_io_timeout` remained.

The next correction distinguishes a cooperative200ms scheduler budget from a
real connection deadline, retaining one bounded read/output batch rather than
discarding accepted bytes/ACKs. See [TCP cooperation](native-tcp-cooperation.md)
for exact limits and negative/positive regression evidence. It does not extend
protocol deadlines, retry ambiguous writes or open a replacement connection.

`02a6524` / `b-251e1726b689`, active138, passed CI5991tests/1skip and Image.
A real `phase=output` scheduler yield occurred while the same MSE observation
continued, confirming that this scheduling event no longer destroys the route.
However the observation ended after153.9s (297messages/112764761bytes,
max gap2.01s, trailing silence1.86s) with a local `TimeoutError`; another local
timeout occurred later. Therefore sustained acceptance remains OPEN.

Decoder timeout diagnostics now distinguish the existing fixed local signatures
`stdin_timeout` (input writer) and `progress_timeout` (no new decoded frames).
No raw exception text or new timeout/buffer policy is persisted/applied.45focused
tests passed under192MiB/no swap, peak98.7MiB. This stage split is needed before
changing the writer or output-drain behavior; the observed generic exception
alone cannot justify increasing another deadline.

`0cfd50d` / `b-f915c1b68689`, active141, retained delivery but a further observation
ended after139.9s (272messages, max gap2.8s, trailing silence4.92s) with
`media_idle_timeout`. FFmpeg's last samples showed a flat input counter and
essentially flat CPU, with no disk-blocked state. This instance was a real
complete-message receive silence, not evidence for changing the stdin guard.

Subsequent bounded SDK inspection found ongoing periodic meter transmission
missing from the continuous implementation. The new same-connection maintenance
owner is documented in [TCP MTP maintenance](native-tcp-maintenance.md). This
addresses that protocol omission; live confirmation is still required.

Latest deployment `3b0b311` / `b-58c43b444dfd`, native/active144, confirmed the
fresh periodic meter receipt for sequence2. CI42103383366 passed6012tests/1skip;
Image42103383491 passed. During unchanged MSE ownership, a read-only lamp request
returned200/verified in175ms; no lamp write was sent. The first closed original
segment probed successfully:300.964s HEVC1920×1080 plus16kHz mono AAC. The next
300s segment used the same encoder-epoch suffix. No OOM/restart loop; sustained
MSE acceptance is still being measured and will be recorded separately.

## Completed bounded continuous acceptance

The latest observer completed its full900.0seconds on one MSE connection:
1797binary messages,684417887bytes, max inter-message gap3.02seconds, trailing
silence0.73seconds at the deliberate test deadline. There was no reconnect,
native retry event, SDK/source replacement or container restart during that
window. Source selection remained native/active144, build `b-58c43b444dfd`.
Messages are MP4 initialization/fragments, not decoded-frame counts; gaps are
delivery intervals, not measured camera-to-screen latency. Payloads were read
and discarded immediately, not accumulated or written as a capture.

Post-run lamp GET again returned200/verified in404ms, without a write. Legacy
ingress counters were present for the two other cameras and their transports
were not migrated. Native recordings continued across successive300s segments
under the same encoder-epoch suffix, and the closed original clip's codec/
resolution/sample-rate probe passed as recorded above. Container memory sampled
around198–228MiB, OOMfalse, restart count0. No sound/light/motor/reset test.

This bounded continuous acceptance PASSED. Do not infer multi-day endurance,
unobserved failure coverage or concurrent native-camera support. The next
implementation item is the shared-account multi-camera owner, with offline
isolation/cleanup tests before any separately authorized additional live camera.
