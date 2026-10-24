# Server-owned native runtime — 2026-10-24

Status: integrated into lifespan, tested offline; not deployed. No production hub
or camera was contacted, restarted, configured, illuminated or sounded.

## Ownership, not automatic activation

Lifespan constructs one `NativeSourceRuntime` with the same `ViewerGate` used by
the media routes. `app.state.native_sources` references its initially empty
`SourceRegistry`. No worker thread, driver factory, encoder or camera connection
is started by construction. Authenticated native sockets reject unclaimed IDs
with1013 rather than accepting and waiting for a source that does not exist.

Internal callers can use `runtime.activate` only with a matching gate/camera,
authentic owned-ingress retirement receipt and reviewed per-device driver offer.
All prior admission, readiness, reconciliation and publication requirements still
apply. No HTTP activation endpoint, browser override or startup scheduler is added.

The runtime retains each single-use activation **before** preparation/submission,
including failed preparations. Default capacity is one (maximum32); duplicate IDs
and capacity overflow are rejected. This limit does not override a driver's
resource domain: Yoosee currently serializes native ownership across its shared
account/control domain. It is not safe to invent independent domains per camera.

## Lazy source executor

`SourceExecutor` has at most capacity threads and capacity queued jobs. There are
no default-pool source tasks or per-viewer workers. A failed thread start cannot
accept an orphan job. Shutdown closes admission before canceling queued work;
callbacks execute outside the executor lock. A queue-timeout race cannot retire
the last worker while an accepted job remains queued.

A completed Future is not sufficient evidence of thread retirement: completion
callbacks may still be executing. Shutdown joins the actual owned threads and
checks pending jobs. Python cannot safely kill an uncooperative thread; deadline
expiry reports `SourceCleanupUnconfirmed` and retains ownership instead.

## Shutdown sequence

1. Close runtime/executor admission and signal every retained activation, even
   one whose prepared session has not yet been created.
2. Close registry admission and signal all sources. Cancel queued jobs without
   constructing their driver factories.
3. Await in-flight preparation. Publication rechecks cancellation before releasing
   viewers, including a stop arriving during the route-publication callback.
4. Close retained sessions, drain registered viewers and join source-owner threads.
   A cleanup failure does not skip other owners while deadline remains.
5. Clear ownership only after confirmed completion. On uncertainty retain handles,
   blocked reservations and closed admission. A subsequent stop may retry delayed
   cleanup; it cannot optimistically erase a blocked resource reservation.

Runtime shutdown defaults to30s, with bounded lock/Future/drain waits. This is not
hard preemption of arbitrary driver callbacks or synchronous database operations;
those must retain their own finite I/O limits. Cancellation is a nonblocking
signal, never a cleanup receipt. A canceled job that never constructed a factory
and holds no reservation is resource-free, unlike an uncertain constructed owner.

Lifespan runs synchronous service cleanup off the ASGI loop, allowing registered
WebSocket consumers to complete while shutdown waits for their leases. Existing
reverse cleanup order stops control/recovery/background services first, then the
native runtime, explicit owned-RTSP runtime, recorder and media service. Every service stop is attempted even
if another fails; failure propagates rather than being reported as clean shutdown.

## Evidence and remaining deployment work

44 focused executor/runtime/activation/lifespan tests passed at138.3MiB, plus94
native-delivery/main/auth/browser/registry regressions at142.5MiB. Both ran under
192MiB, zero-swap, 50%-CPU scopes. Ruff and mypy (336 source files) passed.
Fixtures cover blocked publication, cancellation before session creation, queued
jobs, callback retirement, late source cleanup and one failed owner not skipping
another. Synthetic MP4 is structural test data, not physical playback acceptance.
The two-viewer socket test now waits for each ASGI handler to complete, avoiding
a TestClient context-cancellation race observed while repeating these regressions.

At commit `ba53d09`, [CI](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40085046263)
passed5014 Python tests (one skipped), all frontend contracts, lint and typing.
[Image](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40085046246)
also passed the amd64 build and restricted worker smoke. This is not native
physical-camera, long-running browser or ARM64 runtime acceptance.

The runtime owns only explicit native activations, not externally prepared
owned-RTSP workers or the legacy shared hub. A separate
[owned runtime](owned-runtime-lifecycle.md) now owns RTSP runs that it explicitly
submits. Neither runtime restores active/blocked
policy after restart, schedule migrations or resume native viewing after driver
foreground-control yielding. First shared-hub migration requires a separate
controlled closure procedure affecting all shared consumers. Native audio/archive,
reviewed production evidence, real browser endurance and ARM64 acceptance of this
new lifecycle remain pending. No local container build/restart was performed.
