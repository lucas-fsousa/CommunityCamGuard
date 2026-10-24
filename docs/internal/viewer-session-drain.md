# Browser admission and registered-operation drain — 2026-10-23

Status: integrated into the app lifecycle and primary/temporary/native socket
adapters; offline-tested, not deployed. No source migration or camera operation.

`media/viewer_gate.py` keeps at most64 active leases by default (maximum256) and
32 held camera IDs. IDs are opaque, with no driver or credential inference.
Lifespan supplies one `app.state.viewer_sessions` gate. Standalone router users
must supply their own gate for coordinated drains; no process-global gate is
implicitly created. Leases cover signaling/MSE sessions and synchronous recovery.

The ASGI adapter acquires a lease before starting the guarded operation and
releases it only after `run_guarded` has awaited its work/guard tasks. A coordinator
thread calls `suspend(camera_id, timeout=...)`: under one condition lock it closes
new admission and signals cancellation to existing leases, then waits for their
release. The normal session watcher handles cancellation without blocking the
ASGI event loop. Synchronous recovery requests are counted until their actual
hub request returns. No new per-viewer thread or polling service is created.

Timeouts are finite, positive and at most30s (default8s). A failed drain retains a
blocked hold; late cleanup alone does not permit resume. An explicit successful
retry is required. Concurrent drains of the same ID are rejected, capacity stays
bounded, and a held ID can be retried even at capacity. `resume` requires confirmed
drain; it is not an authorization to select/restart a source. Other IDs' leases
are not cancelled. The synchronous drain must never run on the ASGI event loop.

## Boundary, not a physical receipt

Suspension counts all registered operations, including one admitted immediately
before the gate closes. That operation may reach the hub while draining, but the
coordinator cannot receive a drain receipt until its registered task has finished.
The held gate rejects later admission. Durable policy/recording/binding barriers
remain separately required, as does serialization of runtime reconciliation.

A drained signaling socket does **not** prove an independently negotiated WebRTC
peer is closed, an upstream producer is retired, a camera accepted TEARDOWN, or all
external clients were covered. Those need the verified physical ingress boundary.
No automatic source factory, new API, global hub restart or native startup exists.

## Evidence

Tests cover per-camera cancellation, bounded capacity, double-drain rejection,
timeout/retry latching and release idempotency. An operation deliberately stalls
inside its cleanup; the drain cannot finish until that cleanup is released.
Primary and temporary ASGI sockets deliver synthetic bytes, drain/close their fake
upstream, reject replacement/recovery while held and resume afterward. No camera
traffic, video/audio frames or media processes are used. Existing native/session/
origin/auth-inventory and lifespan tests retain their authority checks.
