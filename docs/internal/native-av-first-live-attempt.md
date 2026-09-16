# First camera-3 native AV operator attempt — 2026-09-15

## Actual execution

The camera-3 registry/enrollment association was checked against the private RE
inventory before activation. Exactly one authenticated, empty, direct-loopback POST
was issued to the existing server's native AV diagnostic endpoint. Login secret
and cookie stayed in client-process memory and were not printed or persisted.

**Result: HTTP 502 after 4.26 seconds**, with the sanitized response
`native AV diagnostic failed; attempt consumed`. No retry was made. This is not
a confirmed native video session: no media counts, AV CLOSE receipt or B9 release
receipt were returned. The generic error does not establish which phase failed;
do not infer stale credentials, rendezvous failure, unsupported codec or camera
overload from elapsed time alone.

## Deployment and safety observations

- App image built with the cached legacy Docker builder capped at 512 MiB, no
  additional swap and one CPU. Build context was 3.871 MB and excluded secrets/data.
- Only `ccg-app` was recreated, first for the temporary opt-in and then to remove
  it. These application restarts interrupt recorder ownership; no zero-downtime
  recording claim is made. go2rtc and the other WSL projects were not restarted.
- The temporary ignored Compose override was changed to disabled/empty values
  immediately after startup. Following the attempt the standard Compose app was
  restored. Runtime verification confirmed diagnostic disabled and target cleared.
- `/health` returned OK. The deployed build endpoint reported `b-d2f903bd9381`.
- After final restart each of the three base streams had exactly one producer.
  Two subsequent observations showed received bytes increasing on all three. The
  counters reset around app recreation, so compare only post-restart observations.
  This verifies server ingress progress, not browser smoothness or native decoding.
- One observed memory sample was approximately 109 MiB for the app and 343 MiB
  for go2rtc, both under their existing 1 GiB limits. No WSL failure was observed.

No microphone audio, siren, light or movement was requested. Other cameras were
not selected by the diagnostic. Normal production RTSP services continued handling
all registered cameras after the application restarts.

## Observability correction before another attempt

The original HTTP error was deliberately sanitized but omitted necessary phase
evidence. `av_route.py` now logs a fixed stage label, outcome, exception class,
release-attempt/receipt flags and elapsed milliseconds after local cleanup. Stages
cover bind, access session, target validation, rendezvous, metering, AV receive/CLOSE
and route release. A failure retains its original phase even while cleanup runs.
No exception text, device ID, endpoint, cookie or wire payload is logged.

Five new tests assert phase attribution, cleanup evidence, success distinction and
non-disclosure of exception contents. This change does not retry or change protocol
behavior. Full Python 3.12 reproduction passed 1,739 tests with one Node-dependent
test skipped in the capped container; the Node contracts passed separately on the
host. Ruff and Mypy (186 files) passed. The telemetry patch is **not yet deployed**; the running app remains the
image used above with the diagnostic disabled.

Next: confirm the correction's CI, then deploy the observability update and explicitly
rearm for one reviewed attempt. Inspect the resulting phase/cleanup evidence before
changing timeouts, credentials or protocol framing. Do not mark native streaming
homologated based on this failed first run.

Follow-up execution and bootstrap attribution are recorded in
[native-av-bootstrap-investigation.md](native-av-bootstrap-investigation.md).
