# Native multi-camera ownership

## Implementation checkpoint — 2026-11-06

Production admission remains **one native camera**. Cameras 1/2 are still legacy
RTSP; no live multi-camera migration or extra SDK login has been attempted.

The broker now has a bounded `AccountMediaRoutes` owner, used by `BrokerAccount`
and its dispatcher. Explicit internal `media_capacity=3` allows three different
devices under the same certified account and sequence allocator. Default capacity
is still one. This is not a capability grant or a public configuration switch.

Implemented and tested offline:

- Each device has its own OPEN, relay correlation and CLOSE receipt.
- Account-level receive dispatches a validated broker frame to its bounded routes;
  the worker no longer separately dispatches that same frame to its initial route.
- Link IDs are allocated without reuse within an account generation, using two
  scalars, not a growing history. Late A3 replies cannot select a replacement's
  relay. Exhaustion fails closed before another OPEN; it does not wrap/reuse IDs.
- Clean/unopened terminal routes release their slots. Duplicate devices and
  capacity overflow fail before OPEN. Uncertain broker release fences the account
  and cancels the other routes, preserving the existing retirement rule.
- All routes must share one output budget. Each has an independent OPEN/CLOSE
  reserve; ordinary traffic cannot borrow another route's, PTZ's or siren's
  cleanup allowance. Traffic burst/rate limits remain unchanged.
- Drain/retire cancels every route. Drain waits for every CLOSE receipt; a receipt
  for one route cannot release another. Account-owned certification is dropped
  on retirement; unfinished jobs retain only their existing cleanup ownership.

Targeted first regression run: 110 passed, peak 102.3 MiB; mypy passed the five
changed source modules, peak 127.2 MiB. Both ran with CPU50% and memory limits.
These are simulated broker tests, **not** concurrent real-camera acceptance.

## Remaining delivery steps

The account worker now also accepts an explicit internal tuple of additional
`MediaBootstrap` entries, only with continuous TCP/account policy and a matching
capacity supplied before starting. `AccountMediaChannel` owns each independent
TCP pump and delivery. Different channels cannot share a delivery queue. The
existing single-camera entry points, defaults and primary result fields remain.

All channels share the previous 200ms TCP scheduling allowance per worker turn
(divided among channels), not 200ms per camera. Each channel still reads at most
4KiB per turn; each connection uses its own existing bounded queues. PTZ admission
waits for every bootstrapped TCP channel to be ready. No extra broker reader,
login, decoder or background thread is created by the group worker.

Shutdown attempts all TCP and broker closes even after one cleanup raises. Every
channel has a bounded summary. Lifecycle replacement and transient-retirement
checks inspect all admitted channels, so a successful primary cannot conceal an
unconfirmed sibling. The legacy single-camera result remains available.

**Isolated retirement implemented:** in explicit multi-channel mode, a local TCP
transport/protocol failure closes that channel and requests its broker CLOSE on
the existing reader. Healthy siblings continue. The failing channel is never
reopened automatically. An unconfirmed physical close or broker release still
retires/fences the whole group. Unknown cleanup exceptions escape after all
other channels have been given their cleanup attempts. Single-channel error and
retry semantics remain unchanged.

**Not yet implemented:** independent per-channel retry/replacement, per-camera
source leases and dynamic join/leave. Do not advertise those as available on the
basis of worker bootstrap tests. Callers must distinguish a retired failed
channel (fixed `socket`/`protocol`/`ended` summary) from a healthy channel.

Second checkpoint: 257 targeted regression tests passed, peak104.9MiB. Full local
mypy passed407source modules with a100.7MiB peak; repository Ruff passed. The first
broker commit `02975e6` also passed GitHub CI6031tests/1skip and the Image workflow.
No production rebuild or admission increase follows solely from these offline
multi-channel results.

1. Connect per-camera prepared sources to a driver-owned shared account lease;
   match enrollment/account identity explicitly. Do not turn the generic source
   registry into a vendor-specific singleton or raise its capacity independently.
2. Implement dynamic attachment and safe per-channel replacement after retirement.
3. Offline source admission, failure isolation, shutdown and consumer tests; then deploy
   on camera3 only. Additional real-camera migration needs explicit scope.

## Separate endurance finding

The previous 900-second MSE acceptance remains valid for that interval, not for
the whole night. The persisted log contains a later consumer `progress_timeout`
at 2026-11-06T02:36:21Z followed by a busy retry. At 04:01Z the source API reported
native/active144 and Docker reported OOM=false/restarts=0. API state alone is not
proof of media progress. Preserve this finding; do not mark prolonged endurance
complete from the earlier 15-minute result.

A subsequent 30-second read-only MSE observation completed without reconnect:
31 binary messages / 8,728,455 bytes. This confirms delivery during that later
interval, not uninterrupted operation throughout the overnight gap.

Third checkpoint:260targeted tests passed, peak105.2MiB. New regressions prove
healthy siblings keep progressing after a channel failure, missing broker release
retires the group, and primary success cannot allow lifecycle reauthentication
when a sibling's cleanup failed. Commit `0bb36d0` passed GitHub CI6041tests/1skip
and Image.

## Deployment checkpoint

Code `39a0f29` passed CI6044tests/1skip and Image, and was deployed as
`community-cam-guard-app:wsl-native-multi-worker` (image487624920ee7), build
`b-d4580bc96801`. Only the app container was replaced; the hub/tunnel were not
restarted, and the user's recordings-host mount edits were preserved.

Camera3 resumed native/active147. A single authenticated MSE connection completed
60.0seconds:63binary messages/20310557bytes, maximum inter-message gap1.62s, no
reconnect or new retry event. These are fragments, not decoded frame counts or
end-to-end latency. First periodic meter ACK was observed on the new connection.
The new archive epoch is `ea1543da82e5c46d1aedeed323cd8eee5`; a current UTC-hour
file exists. Legacy counters advanced5690→9178 (yard),3838→6232 (garage), with
one base consumer each. App181.1MiB/1GiB, OOM=false/restarts=0. Three FFmpeg
processes were inside the app; this does not count hub-owned transcoders.

Production still admits one native source. This live test exercises the refactored
single-camera path; it is **not** a three-real-camera acceptance. The next delivery
step is shared source ownership/dynamic attachment, not repeating physical controls.

Operational observation: during replacement Uvicorn waited for active connections
before lifespan shutdown; Compose has a45-second stop grace. Clean service teardown
was not established from that wait. Do not infer graceful archival finalization
from successful namespace-fenced startup; investigate bounded connection draining
separately if necessary. No shutdown-budget change was mixed into this delivery.

The first closed new-epoch clip was inspected without playback/transcoding:
`20261106_042024_ea1543da82e5c46d1aedeed323cd8eee5.mp4`,300.266667seconds,
HEVC1920×1080 and AAC16000Hz/mono. The following042524clip was created under the
same epoch. This verifies segmentation on the updated single-camera path.

CI follow-up: the docs-only run42162237524 remained in the unconditional apt
FFmpeg-install step for over five minutes, before any tests. The workflow now
reuses existing ffmpeg/ffprobe when both exist; otherwise update/install each
have240second wall-clock limits and bounded network waits/retries. Required
ffmpeg/ffprobe/prlimit checks and the complete test suite remain mandatory.
