# Server-owned explicit RTSP preparation — 2026-10-24

Status: wired into lifespan and tested offline/loopback. No production container,
camera, source policy or enrollment was changed. No physical tests were performed.

## Idle wiring and authority

Lifespan creates one empty `OwnedIngressBindings`, shared by `Go2rtc`, `Recorder`
and `OwnedSourceRuntime`. Its retirement coordinator uses the same viewer gate,
recorder gate and reconciliation guard as these consumers. No owned worker,
connection or executor thread starts at boot. Current legacy feeds are unchanged.

`runtime.activate(receipt, unstarted_worker, apply_routes=...)` is internal only.
It requires a matching owned-RTSP transition and the exact single-use receipt
issued by **that runtime's** retirement coordinator. An equivalent reconstructed
receipt cannot launch a worker or change intent. The supplied worker must be an
unstarted `OwnedRtspIngress` exclusively entrusted to this owner, not an external
running process. No source-selection HTTP endpoint or arbitrary browser URL is added.

This is **not the first legacy migration**: the existing coordinator can issue a
receipt only after retiring an already-owned ingress. It cannot certify closure
of the current external shared hub. That still needs a controlled procedure and
verification covering all affected consumers and the actual hub namespace.

## Ordered preparation and publication

1. Retain the prepared owner before receipt claim or submission. Capacity defaults
   to one (maximum32); failed and completed runs remain reserved until shutdown.
   There is no automatic same-ID replacement, fallback or unbounded failure list.
2. Claim the authentic receipt under reconciliation exclusion, checking durable
   revision and both confirmed consumer holds. Submit the run to the existing
   bounded lazy `SourceExecutor` and track its Future before cleanup can proceed.
3. Wait for the actual worker's PID and generation-specific loopback listener.
   Default readiness deadline5s, maximum30s. No extra direct-camera probe/encoder
   is used. Child-listener readiness is not decoded-video/browser acceptance.
4. Recheck run liveness/cancellation; CAS owned intent active, attach its matching
   ready binding and invoke the trusted bounded hub-publication callback. Recheck
   again, then release recording and viewer holds. New consumers use the same
   owned base restream; recording keeps the existing video-copy/UTC behaviour.

Callback, publication and cleanup share the prepared owner's state lock, but no
Future wait holds it. Stop arriving during publication cannot reopen consumers.
Preparation errors stop/reap the worker and leave admission held/intent blocked,
never optimistically restore the camera's direct URL.

## Completion, generations and shutdown

A completed owner holds viewers, withdraws its binding and blocks only its current
durable revision. A policy read transaction spans the hold so a delayed old
callback cannot hold viewers after a newer native/owned generation was published.
Binding withdrawal/discard can additionally require the exact worker identity:
old cleanup cannot remove a newer binding under the same public camera ID.

The runtime retains `OwnedRun` handles for handoff through its coordinator. A
successful owned-to-native handoff leaves the old handle retained until shutdown;
subsequent old-owner cleanup does not block or drain the new native generation.

Shutdown closes admission, signals all workers, cancels queued jobs and waits for
in-flight preparation. It closes each retained owner and joins actual executor
threads, not merely completed Futures. Unknown child exit/drain keeps handles and
admission closed; one failure does not skip other owners while time remains.
Late confirmed cleanup can be retried. Completed/failed runs are not reused.

The30s runtime budget bounds coordinator/Future/lock waits, not arbitrary synchronous
publication callbacks, SQLite calls or the recorder's existing cleanup I/O. Those
retain their own limits; Python thread termination is not used as a fake receipt.
Confirmed close is idempotent. On startup the new empty registry does **not**
restore active/blocked owned intent or open the camera directly.

Lifespan stops background services first, then native runtime, owned runtime,
recorder and media. Cleanup stays off the ASGI loop, and every service is attempted
even after another fails. The independent native runtime still owns only native
runs; this service owns the RTSP workers that it explicitly submits.

## Evidence and next boundary

Tests use synthetic workers and one pinned real go2rtc child connected only to a
loopback RTSP metadata fixture. Two consumers share one fixture connection; stopping
the runtime confirms socket closure and child reap. It emits no audio/video frames.
Other cases cover forged receipts, publication order/failure, readiness failure,
stop during publication/claim, retained uncertain children, stale binding cleanup,
handoff to synthetic native output and idle lifespan wiring. Tests run in bounded
192MiB/no-swap/50%-CPU scopes, not a local full-suite or Docker build.
Focused groups of48 lifecycle/activation,80 binding/retirement/browser and38
owned/recording/reconciliation cases passed at167.4/155.3/125.6MiB respectively,
all without swap (groups overlap). Ruff and mypy (339 source files) also passed.

Remaining: controlled first shared-hub migration, restart/restore orchestration,
foreground-control/native resumption, reviewed per-device native evidence, native
audio/archive and physical browser/endurance/ARM64 acceptance. This stage does not
enable native or owned switching in the deployed dashboard.
