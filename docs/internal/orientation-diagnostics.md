# Orientation diagnostics

As of 2026-10-18, orientation operations emit bounded structured events to
`data/logs/control-events.jsonl` (beside the configured database). Docker's existing
data mount preserves them across app container recreation. UTC timestamps, opaque
camera ID, random operation ID, stage/outcome and numeric error only; no credentials,
native responses, requested settings, addresses or exception messages are recorded.

Stages distinguish session establishment, preflight, write, readback and completion.
An unchanged value records `unchanged`, not a successful physical movement. Canonical
API handler failures additionally record the projected HTTP status, including busy or
missing capability evidence. Authentication/local-only/validation rejections before
the route handler are outside this instrumentation; this is not a full HTTP audit.

Rotation retains the current 1 MiB file plus three backups (about 4 MiB total).
Retention is size-based, not a guaranteed number of days. Disk errors do not abort a
camera command. Startup warns if persistence cannot be opened. This scoped sink does
not persist all application logs and cannot recover old removed-container logs.

54 focused tests passed with97.5MiB peak/no swap: failure stage, safe serialization,
append/reopen, idempotent setup, rotation, disk failure and public error handling.
No protocol behavior or retry timings were changed in this diagnostic step.

The incident from October17 cannot be retrospectively attributed: the app container
was recreated on October18 and its former stdout logs were not retained. Live
reproduction results must be recorded separately rather than claimed as its cause.

## Live reproduction, October18

Deployed187e9ac/imageef1401aac7d2 after CI39134717696 passed. Build512MiB,
no additional swap/half CPU; only app recreated. Initial operator probe failed
before startup completed; a subsequent probe lacked the required label capability
code and returned422 without camera action. Corrected to the recorded camera3 label.

Camera3 read returned normal. First cycle's inversion and restoration requests both
returned409 at18:47:23–24UTC, with only API-rejection events and no orientation-session
event: neither reached the orientation setter. A subsequently inspected capability
snapshot was fresh/supported. This suggests a transient availability/refresh window
but does not distinguish that from camera-busy contention; the first harness did not
retain the projected error detail. Do not call this the proven cause of yesterday's
failure. Diagnostic API events now also retain an allowlisted exception category
(`ControlBusy` versus `ControlNotReady`, etc.), never exception text.

A second finite cycle at18:48:02–11UTC returned200/verified/changed for inversion
and restoration. Independent final property read confirmed normal.13.931s operator
elapsed,45.7MiB peak/no swap. Both operation IDs and completions are retained in
`data/logs/control-events.jsonl`. No audio, no other-camera action. Physical visual
confirmation has been requested; protocol confirmation is not its substitute.
