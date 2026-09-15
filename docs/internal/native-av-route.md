# Experimental native AV route ownership — 2026-09-15

`p2p/av_route.py` now composes authenticated camera-session preparation, one direct
rendezvous, media metering, the bounded AV probe, and separate B9 route release.
`p2p/av_route_io.py` contains the small deadline/traffic-budget socket facade so
the orchestration module does not absorb transport mechanics.

## Ownership and bounds

The operator-supplied reviewed camera ID and vendor device ID must match the durable
enrollment before any socket allocation. The authenticated inventory target is
checked again before direct rendezvous. These checks are not a hardcoded camera-3
allowlist: the future internal experiment entry point must select camera 3 and
hold the application's exclusive camera reservation across this entire call.
There is no browser/API/CLI entry point, automatic capability grant or scheduling.

- A fresh socket is used; production RTSP/intercom sockets are never borrowed.
- Preparation has a 20-second absolute deadline, reception/CLOSE up to 12 seconds,
  and route cleanup a separate one-second budget (up to 33 seconds total, excluding
  OS scheduling delay). Default reception is only three seconds.
- Each phase has a 10,000-datagram / 8 MiB returned-byte / 2 MiB send budget.
  Deadline and cancellation checks surround I/O. Cleanup retains its own bounded
  chance to release the route even after cancellation or phase budget exhaustion.
- The fresh calling attempt is allocated before A4 transmission and passed into
  `call_device()`. Exactly one calling attempt is made. Its ID is retained even if
  sending/receiving raises before `CallingResult` exists.
- The reserved broker sequence for B9 is `node.next_sequence + 2`, independent
  from AV/KCP: one broker A4 and its direct A4 occupy the preceding allocations.
- The AV probe explicitly leaves the socket with this outer owner, while still
  clearing handshake state on every exit. B9 is attempted in `finally`, then the
  underlying socket is always closed. B9 is attempted even after ambiguous opening,
  metering or AV errors, and even when the AV layer cannot complete CLOSE.
- Strict B9 receipt checking requires the node session ID, emitted sequence and
  mode-2 transport receipt. This is an opt-in for this new path; legacy callers
  retain their existing behavior. Short frames are rejected before field access.
- Successful results require both the AV probe result and B9 transport receipt.
  Missing release receipt is an error, not an inferred release. Errors during
  primary work remain errors even if cleanup succeeds. No automatic retry follows.

No token renewal, old AV initializer, speaker/microphone path or camera setting
write is used. Transport receipt still does not prove actual camera resource
release; that remains part of live observation.

## Evidence

Tests cover release-before-socket-close ordering, retained link ID after ambiguous
failure, mismatched targets, cancellation, unconfirmed/erroring release, per-phase
deadlines and traffic budgets, strict B9 session/sequence/mode matching and borrowed
socket ownership. All 201 selected tests passed, including existing rendezvous,
intercom and intercom-stream regressions. Ruff and Mypy (182 backend source files)
passed. Python checks ran serially with 512 MiB address-space limits.

No camera was contacted, no browser/emulator/decoder was started, and no container
was rebuilt or restarted. This is mocked orchestration evidence, not a successful
live native stream or a full-suite certification.

## Next

Review meter/keepalive requirements and add the internal camera-3-only invocation
under the actual application's camera-operation reservation. Then run one bounded
three-second sample, checking media counts, AV CLOSE and B9 receipts and whether
the existing RTSP producer remains healthy. Do not start several retries or enable
native video in the dashboard on the basis of these tests.
