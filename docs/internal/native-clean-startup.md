# Clean native server restart —2026-10-29

Native intent is not proof of retirement. `media_native_restart` stores a one-use
camera/revision/domain checkpoint only after every native source, viewer drain
and executor confirms exit. Only previously published sources qualify. No tokens,
camera URLs or feature grants are stored. Uncertain cleanup issues no checkpoint.

`NATIVE_RESUME_ON_START=false` remains the default. With explicit opt-in, ordinary
startup checks storage before capture and asks the selected driver for its normal
original-A/V archive offer. Missing/revoked/expired evidence leaves the clean
checkpoint unspent and the native source unavailable, never falling back to RTSP.
A matching checkpoint is consumed atomically with a blocked→transition revision
change, converted into a process-local retirement receipt, and passed through the
existing readiness/publication path. Source launch invalidates any old checkpoint
before factory invocation. A crash after consumption cannot replay the permission.

Clean shutdown creates the next checkpoint only after source/executor cleanup.
Failed construction/readiness does not resurrect the spent checkpoint. Wrong
camera/domain/revision and competing consumers fail closed. The server's existing
OS-held database ownership lock remains mandatory around the entire lifecycle.

98 focused startup/runtime/retirement/DHCP/operator tests passed under192MiB/no swap/
50% CPU (146.7MiB peak). A normal ASGI test restores a source across two complete
lifespans and proves the new generation, spent old checkpoint, storage-first order
and absence of parallel legacy capture. Negative cases cover disabled opt-in,
intent without a clean checkpoint, uncertain cleanup, failed factory, stale review,
domain/camera mismatch and a two-thread consume race. Mypy366 files/Ruff passed.

DHCP recovery now observes only active legacy source policies, not missing native/
owned routes in the shared hub. Driver-native recovery remains responsible for
those transports; this does not implement native endpoint rediscovery.

The bounded host trial supports `--restart-server` with reviewed A/V and no motion,
storage or session-retirement actions. It keeps the original deployment stopped
between two server lifecycles on the same private clone. The second lifecycle uses
ordinary application startup, not a diagnostic activation/factory. Its first clean
stop marker is moved aside before starting again, so it cannot certify cleanup of
the second server. Retention deletion/pretranscoding stay disabled in the clone.
Server/monitor code moved into a separate diagnostic module to keep the launcher
small. These are two fresh server lifecycles in one bounded host process, not a
hard-kill/power-loss acceptance.

## Actual camera3 restart evidence — V/W

V delivered initial H264/AAC but ended with an intermittent account-worker
`protocol` failure and missing TCP CLOSE receipt before clean restart. It issued
no checkpoint and no clean stop marker. Rollback waited for whole-group exit;
production returned. This is negative fail-closed evidence, not a passing restart.

W completed the first60s with source revision2, two other recorders and FDs31.
The first server confirmed shutdown and saved its checkpoint. The second ordinary
ASGI startup consumed it, prepared/published the reviewed driver source at revision5
and delivered H264/AAC through the authenticated existing player socket. Independent
observers received three binary messages in each lifecycle; both other recording
statuses were active. No second legacy handoff, diagnostic source construction or
new vendor-app login was used for the restored lifecycle.

The second source later encountered the same protocol/missing-CLOSE condition
after its10s sample. Consequently the complete60s+60s clean-shutdown acceptance
did NOT pass, and no second clean checkpoint may be issued. The original deployment
was restored only after the group exited. W peaked at225.1MiB/no swap under768MiB/
100% CPU,42.062 CPU seconds over104.160s wall time; independent observation49.5MiB.
Private artifacts are under ignored `temp/native-restart-20261029-v` and `...-w`.

The real clean restart/readiness is established; sustained post-restart transport
stability remains open. Added payload-free account protocol failure-site metadata
for the next diagnosis without changing protocol validation/retry classification.
The operator now exits with an error if Uvicorn returns without a confirmed clean
stop marker, rather than letting a zero process status imply successful cleanup.
No primary boot opt-in/profile/source-policy changes were made.

Follow-up X/Y isolated the intermittent failure to `KcpReceiver.expire()`:
`session_protocol_code=kcp_assembly_timeout`. Labels are allowlisted, never raw
exception messages. Static review and a synthetic clock regression reproduced a
deadline bookkeeping defect: after finishing an assembly, retained future sequence
numbers kept the previous assembly's age. The next message could expire less than
two seconds after real completed-message progress. Resetting the assembly origin
only when a message completes fixes that case. Partial progress, duplicates/ACKs
and out-of-order arrival alone still cannot renew it; byte/segment limits and
the two-second timeout are unchanged. The repaired paired acceptance then passed
in Z below.

## Repaired paired acceptance — Z

Both60s server lifecycles completed with zero native reconnects or protocol errors.
The normal second startup consumed the first clean checkpoint and published revision5
after revision2. Independent authenticated player probes delivered H264/AAC in both
lifecycles, with both other recorders active. Twelve10s samples kept both other
recorders present and FDs31–33. Both clean stop markers, original-container restore
and directory-owner restore are present. The private database ends at native/
blocked revision6 with exactly one clean restart checkpoint for revision6: the old
permission was spent and a new one issued only after the second confirmed stop.

The original1920x1080 HEVC /16kHz mono AAC recordings survived as separate epochs,
with video durations63.08s and62.68s. The complete group peaked at251.2MiB/no swap
under768MiB/100% CPU,80.260 CPU seconds over147.416s wall time. The independent
socket observer peaked at49.3MiB. Private artifacts are under ignored
`temp/native-restart-20261029-z`. This is two fresh server lifecycles in one host
process, not hard-kill/power-loss or multi-day acceptance.

The KCP regression group passed79 tests under192MiB/no swap/50% CPU (122.8MiB peak).
CI at `b21c8ca` passed5417 tests (1 unrelated existing skip), all Node contracts and
Image smoke. Mypy366 files/Ruff passed. The bounded clean server-restart gate is
now accepted; do not repeat V/W failures as though no restart ever worked.

CI caught missing configuration-inventory classification and the old relocated
launcher AST test; both were corrected in `0bdacd8`. CI then passed5412 tests
(1 unrelated skip), Node contracts, with the runtime Image smoke already green.

No primary native selection/boot opt-in is installed by this change. Identity
evidence maintenance is next before permanent use; production rollout, abrupt-loss
recovery and multiple native routes remain separate gates. Existing short snapshot
validity is deliberately not bypassed.

## Saved WSL deployment

Runtime `449f0a0` is deployed as build `b-653cb06c9678`, image
`community-cam-guard-app:wsl-449f0a0` (`b5aa8269ae82`). The clean git-archive build
used512MiB/no extra swap/50% CPU and only the app was replaced after green CI/Image.
The previous `wsl-eb1f930` image is retained. Local/public HTTPS health/build,
primary login/unauthenticated rejection and growth of all three recording files
passed. Containers show no OOM or unexpected restart.

Production explicitly remains `native_resume_on_start=False`, zero clean native
checkpoints and three active legacy revision0 selections. Trial profiles/checkpoints
were not copied to the primary database. There are no running physical test tasks.
Next implementation is shared-session identity evidence maintenance before enabling
permanent native selection; do not bypass the existing freshness/cleanup guards.
