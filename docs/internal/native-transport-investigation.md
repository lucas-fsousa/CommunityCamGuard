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
