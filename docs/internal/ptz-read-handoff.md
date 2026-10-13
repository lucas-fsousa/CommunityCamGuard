# PTZ cache retirement before read-account handoff

2026-10-13 — implemented and offline-tested; no production caller activates this
handoff yet. Existing dashboard PTZ, service scheduling and capabilities unchanged.

## Race addressed

The idle timer removes a route from `PtzRouteCache._idle` before closing its socket.
An empty cache therefore did not prove physical retirement: a timer could still
be closing a removed route when another owner attempted authentication.

Idle shutdown and timer expiry now register in-progress closes under the cache
lock before removing routes. Completion decrements the count and wakes waiters;
failure is retained. Shutdown attempts every detached idle close even if one fails.
No extra thread, process, authentication or timer is introduced.

## Experimental ownership boundary

`native_ptz.reserve_read_handoff()` acquires the existing reentrant broker
reservation, rejects pending movement/warmup, and enters `PtzRouteCache.quiesce()`.
The latter pauses admission, closes idle routes and waits at most one second for
already-running timer closes. Direct socket close remains an OS operation, not a
hard real-time guarantee. A timeout/failed close prevents handoff and future cache
reuse, including when a delayed close subsequently finishes.

The reservation remains held throughout the caller's body: fresh read-account
authentication, worker execution and confirmed physical cleanup must all fit
inside that body. New legacy PTZ/warmup operations cannot perform broker I/O until
the reservation is released. They retain their existing bounded wait/busy behavior.
This is not a mechanism for running the new read worker alongside active PTZ.

The cache context by itself requires external ownership and no active leases.
Use the native-owner boundary, not a bare cache context, for integration. No borrowed
active socket is closed or handed to the new reader.

If retirement or the caller's body raises, the native boundary conservatively
marks the driver-wide broker channel uncertain. Every subsequent
`own_control_channel()` admission then fails; there is no reset endpoint or retry
bypass. This includes interruptions even if cleanup may have succeeded. It is
intentionally broader than one account because the current broker lock is also
driver-wide. Rejecting already-pending PTZ *before* retirement does not poison it.

The future caller must raise on an unsuccessful new-worker close summary rather
than silently returning it as success. Normal read timeout with **confirmed socket
close** can be returned without poisoning; this boundary never replays a command.

## Evidence and remaining integration

71 focused cache/handoff/native-PTZ/control-ownership/maintenance/lifecycle tests
passed under192MiB/no-swap/half-CPU limits, peak99.8MiB. Ruff and targeted mypy
passed. Tests cover real bounded test-thread timer races, delayed close, multiple
idle routes with failure, pending motion/warmup rejection, admission during handoff
and legacy-login blocking after interruption. No camera/provider I/O, deployment,
full local suite or container restart.

Next: compose this boundary with the bounded connection factory and finite worker
inside a server-owned read-only preparation adapter, with explicit result/close
handling and shutdown cancellation. Do not enable background replacement until
foreground handoff and worker lifecycle are integrated; a60-second exclusive
identity-read diagnostic is not a production low-latency PTZ warmup.
