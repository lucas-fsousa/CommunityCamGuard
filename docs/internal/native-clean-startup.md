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
hard-kill/power-loss acceptance. Actual camera-backed acceptance is pending here.

No primary native migration/boot opt-in is installed by this change. First migration,
identity evidence maintenance, abrupt-loss recovery and multiple native routes remain
separate gates. Existing short snapshot validity is deliberately not bypassed.
