# Opt-in same-process AV diagnostic trigger — 2026-09-15

`diagnostics/yoosee_av_api.py` registers the hidden POST endpoint
`/api/internal/diagnostics/native-av`. It invokes the reserved diagnostic in the
actual server process. Generic camera APIs and driver capabilities are unchanged.

## Gates and limits

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
