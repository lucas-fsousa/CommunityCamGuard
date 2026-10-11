# Opt-in same-process AV diagnostic trigger — 2026-09-15

`diagnostics/yoosee_av_api.py` registers the hidden POST endpoint
`/api/internal/diagnostics/native-av`. It invokes the reserved diagnostic in the
actual server process. Generic camera APIs and driver capabilities are unchanged.

## Gates and limits

### TCP relay mode (2026-10-11; not live-validated in-server)

**Follow-up:** now validated once inside the server: 55 HD frames decoded and both
CLOSE/B9 receipts positive. Restored disabled/empty afterward. The implementation
notes below describe the original offline stage; see the latest
[live checkpoint](native-tcp-cleanup-checkpoint.md). The local HTTP client must
send an explicit same-loopback Origin and a non-form Content-Type (application/json)
for its empty POST; do not weaken CSRF protections for diagnostic scripts.

`NATIVE_AV_DIAGNOSTIC_TCP_RELAY=true` selects the bounded native TCP route.
Default is false. It cannot combine with platform-only mode; optional post-route
decoding is supported. Both camera and shared-account broker reservations cover
authentication, discovery, media and cleanup. This prevents in-process warmup
from replacing the broker session, not interference from external vendor apps,
CLI probes or another server instance using the same account.

Discovery accepts a correlated encrypted broker advertisement, including bounded
fragment reassembly, then attempts only one global IPv4 TCP endpoint. No address
sweep, auto-renewal or fallback is enabled. TCP pairing, media and cleanup are
finite; one B9 attempt happens before physical socket closure even after an
ambiguous A4 send. Decoder input is bounded and wiped after use.

Without decoding the response has `reception` (ready/CLOSE confirmation and safe
frame/byte counts), `broker_release`, and `broker_messages`. With decoding these
are under `route`, alongside `video`. **HTTP 200 and decoded frames do not mean
broker release succeeded**: inspect `broker_release` separately. A false value
remains an unresolved cleanup result. Do not auto-retry it.

81 focused offline tests passed, including encrypted fragmented discovery and
shared-lock failure paths. No TCP activation or container recreation accompanied
this integration. Follow the procedure below only after coordinating the shared
app restart; standalone execution is not an alternative to that deployment.
See the [cleanup checkpoint](native-tcp-cleanup-checkpoint.md).

### Platform-only mode (2026-10-03)

`NATIVE_AV_DIAGNOSTIC_PLATFORM_ONLY=true` selects a bootstrap-only attempt through
the same endpoint, operator target checks, shared camera reservation and single-use
guard. It is false by default. Set decode-video **false**: the two modes conflict
and are rejected before any camera route is opened.

This path authenticates, performs one rendezvous and the existing bounded MTP
measurement exchange, then releases the route with B9 and closes the socket.
It does **not** call the AV probe or send AV INIT/START, allocate a video sample,
run FFmpeg/ffprobe, or invoke audio/PTZ/settings controls. Preparation retains its
20-second budget and cleanup its independent one-second budget; no retry or
extra background connection is added. Route setup itself still contacts the
camera/broker and is not a guarantee of zero camera-resource impact.

The result contains only `device_platform_version` (2 or unknown/null from current
evidence), `meter_roundtrip_confirmed` and `route_release_acknowledged`. Unknown
is a valid outcome: do not auto-retry, infer platform 1 or activate HD. Missing
roundtrip or release receipt fails instead of reporting success. No AV CLOSE is
needed because this mode never invokes AV INIT; B9 receipt still does not prove
physical release of every remote resource.

The existing procedure below still applies: camera 3 only, same server process,
bounded memory, account for any deployment's recorder interruption, and remove
all temporary diagnostic settings afterward. Browser parameters cannot select
this mode. Source implementation has 68 focused passing tests, peak 91.1 MiB
without swap in a 256 MiB/50%-CPU cgroup.

Live checkpoint, 2026-10-03: one same-process camera-3 request returned HTTP 200:
`device_platform_version=null`, `meter_roundtrip_confirmed=true`, and
`route_release_acknowledged=true`. The exact device ID and MAC matched the private
inventory and enrolled registry before activation. No AV stream, decoder or camera
control was invoked. Unknown remains unknown; this does not unblock HD selection.
Do not repeat the same observation expecting a different platform inference.

The client cgroup completed in 4.042 seconds, peak 9.1 MiB without swap (client
only, not the server's memory). App-only recreation was required to arm/disarm;
the disk override was reset before the request. After restoration both opt-ins
were false, target fields empty and health HTTP 200. Three recorder MP4s grew
1,310,720 bytes in aggregate over ten seconds. go2rtc was not recreated. The
two app restarts briefly interrupted recording; no zero-gap claim is made.

### Existing AV mode

- Disabled by default: `NATIVE_AV_DIAGNOSTIC_ENABLED=false`.
- Optional post-teardown video decoding is separately disabled by default:
  `NATIVE_AV_DIAGNOSTIC_DECODE_VIDEO=false`. Its ownership/resource bounds and
  extended operator-client timeout are documented in
  [native-av-decode-trigger.md](native-av-decode-trigger.md).
- Existing authenticated dashboard session cookie required.
- Direct loopback peer and existing local Host/Origin checks required. LAN clients,
  forwarding headers and cross-origin requests are rejected. Do not expose this
  path through a proxy that strips forwarding metadata.
- Target comes only from server-side `NATIVE_AV_DIAGNOSTIC_CAMERA_ID` and
  `NATIVE_AV_DIAGNOSTIC_DEVICE_ID`. Shape, registry and enrollment checks precede
  route creation. No query parameters or request body are accepted.
- One attempt per process, claimed atomically before work; busy targets and failures
  consume it too. No automatic retry. No target, duration or credential override.
- Fixed three-second reception under the shared PTZ/audio/control lock, including
  preparation, AV CLOSE and B9 cleanup. Existing resource bounds remain active.
- Shared-account broker reservation is held throughout, including optional decode.
- Only aggregate results returned; expected failures are sanitized.

Single-use state and camera locks are process-local: use the existing single-worker
server, never multiple workers/replicas for this experiment. Restarting rearms the
attempt if the opt-in remains enabled. Client disconnect does not cancel this
bounded synchronous operation; never retry an uncertain HTTP outcome.

## Next live procedure

1. Confirm camera 3's exact current registry/enrollment pair from the private RE
   inventory. Do not configure either installed camera as the test target.
2. Deploy the tested app with those three settings temporarily configured. Check
   memory headroom and account for the app restart's recording interruption. Leave
   go2rtc and unrelated WSL containers untouched.
3. Authenticate through direct loopback and issue one empty POST. Never print the
   cookie or put it in shell arguments. The HTTP request must reach the existing
   server; do not invoke the Python diagnostic from a separate process.
4. Record safe counts and separate AV CLOSE/B9 receipts alongside RTSP health
   before/after. Receipts are not physical teardown or decoded-video proof. Inspect
   the failed phase instead of retrying automatically.
5. Remove the temporary enable/target settings; do not leave the diagnostic armed
   for a future restart. No native-video dashboard capability is granted.

## Evidence

Twelve endpoint tests cover authentication, disabled/missing configuration,
loopback/LAN, forwarding/cross-origin checks, request override rejection, single
use, sanitized failures and busy-camera consumption. All 274 selected tests passed,
including six architecture tests. Ruff and Mypy (186 backend files) passed. Python
checks ran serially under a 512 MiB address-space cap.

The trigger implementation was initially tested offline. A subsequent temporary
activation and one failed live attempt are recorded in
[native-av-first-live-attempt.md](native-av-first-live-attempt.md); the feature was
disabled again afterward. No native-video success is claimed. See
[native-av-meter-diagnostic.md](native-av-meter-diagnostic.md) and
[native-av-route.md](native-av-route.md) for the underlying protocol ownership.
