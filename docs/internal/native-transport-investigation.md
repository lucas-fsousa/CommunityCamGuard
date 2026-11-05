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
