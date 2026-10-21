# Exclusive account read worker — 2026-10-12

`account_read_worker.AccountReadWorker` composes the bounded mailbox/account
dispatcher with encrypted broker maintenance. It **creates no thread**, performs
no authentication and is not wired into production controls or media. A caller
must own the shared-account/enrollment reservations and transfer an exclusive
authenticated socket to `run`; never lend a cached PTZ socket.

## Ownership and bounds

2026-10-20: these time/cumulative limits describe the unchanged default `FINITE`
policy. Explicit [continuous policy](continuous-account-policy.md) now supports
heartbeat-maintained lifetime with replenishing bounded traffic allowances; it is
offline-tested and not selected by any production or diagnostic caller.

- One run per worker/generation; callers only use mailbox submit/cancel/take,
  stop or drain. A second run is rejected without touching the first account.
  Its separately supplied socket is closed; the currently active socket is never
  closed by a duplicate invocation supplying the same object.
- One socket reader, original read deadlines, no automatic resend or recertification.
  Maximum60 seconds after run starts, while the underlying account still retains
  its original120-second lifetime. Queuing before run does not reset read deadlines.
- Eight total mailbox slots, 512 incoming datagrams/1MiB, two fragment groups,
  4KiB wire limit (receive buffer4097 detects oversize rather than silent truncation).
  Independently capped output:512 sends/1MiB, including ACKs and attempted writes.
- Socket reads poll every50ms at most; writes timeout within200ms. Stop is checked
  before writes and after reads; caller cancellation gets a final pre-send check.
  Once a send begins, cancellation cannot recall it. Only identity reads admitted.
- Heartbeat has reserved capacity and the shared sequence namespace. Complete
  decoded values go only to mailbox results, never a second completion queue or
  summary. ACK-send failure does not erase a previously decoded camera response.
- Drain rejects queued/new work, allows already active reads up to2 seconds, and
  schedules no new heartbeat. Repeated drain calls never extend that deadline.
- Every run exit retires the dispatcher, clears fragment/key references and closes
  the socket, including exceptions/interruptions. Close failure is independently
  reported, never concealed by a successful read. Normal60-second completion is
  explicitly `deadline`, not a claim of permanent session health.

## Verification and remaining integration

Synthetic socket tests use the actual encryption/response codecs and fake time,
including concurrent callers with a real bounded test thread, late admission,
out-of-order completion in the dispatcher, cancellation, heartbeat, silence,
input/output flood limits, graceful drain, duplicate run and I/O/interruption exits.
No real camera traffic, credentials, runtime thread or production import was added.
The combined mailbox/account/worker and legacy-pilot regression run passed131 tests
in3.625 seconds, peaking at86.4MiB with zero swap (192MiB/halfCPU cgroup). Ruff and
targeted mypy passed; no local full-suite run, browser, build or container restart.
The subsequent narrow-allowlist/diagnostic regression run passed149 tests, peaking
at106.3MiB with zero swap. Admission shares exactly three identity paths with the
server diagnostic; the existing broader property-reader catalogue is unchanged.

The older `broker_read_dispatch.BrokerReadDispatcher` remains the15-second
single-batch diagnostic; `account_read_dispatcher.AccountReadDispatcher` is the
new mailbox/shared-heartbeat composition. These are intentionally distinct:
do not turn the legacy short pilot into an indefinite account owner.

Next: a guarded server-owned worker check, then generation handoff and migration
of the existing shared PTZ account owner. That migration must avoid a competing
login/reader and retain start/release safety, per-camera enrollment binding and
driver-reported capabilities. Passing these offline tests alone does not authorize
enabling native media/PTZ capabilities or claim LAN-only operation.

## Guarded server check

`BROKER_READ_DIAGNOSTIC_WORKER=true` (default false) selects the real finite worker
on the existing hidden `/api/internal/diagnostics/broker-reads` endpoint. It is
mutually exclusive with `BROKER_READ_DIAGNOSTIC_HEARTBEAT`; neither can be selected
by request body/query. Primary session, direct loopback, explicit server-reviewed
camera/device, empty request and one-use-per-process guards remain unchanged.
Camera and account reservations cover authentication, worker execution and close.

The adapter queues exactly three identity reads for one device, runs the sole
reader for at most60 seconds and verifies three successful application responses,
one correlated heartbeat and no socket-close failure. `failure=null` requires all
criteria; HTTP200 or `worker.reason=deadline` alone is insufficient. `worker` holds
only fixed reason/counters/close status, never raw identities, keys or model values.
It does not claim reads after the heartbeat or concurrent physical PTZ. No thread
or extra login is created; the existing server request worker runs the finite pump.

84 focused adapter/worker/diagnostic/API/settings tests passed (126.4MiB peak,
no swap,192MiB cgroup); targeted mypy/Ruff passed. Live check is pending.

### Camera3 live result

Source `119779b`, image `9851b75f5060`, passed the guarded in-server worker check.
All three identity reads returned `status=response`, `transport_result=0`,
`error_code=0`; `failure=null`. The worker completed its60-second window with
`reason=deadline`, one heartbeat response,24 sent/21 received datagrams and
`socket_close_failed=false`. The separate after-read/heartbeat-detail arrays are
empty in this profile; no post-heartbeat read batch or interval hint is claimed.

One authentication, one socket reader, no new thread or action/media command.
The operator ran71.908 seconds including app-only recreation/restoration, peaked
at108.5MiB with no swap. App usage observed during the check was140.8MiB; this is
not a server peak measurement. Build used384MiB/no additional swap/halfCPU and a
4.95MB context. CI38332460714 passed Python and Node checks before deployment.

All read/heartbeat/worker/AV diagnostic flags were verified false afterward, with
empty read targets, health200 and app running/OOM=false/restarts0. Recreating only
the app briefly interrupts recordings; this does not establish gap-free deployment.
The new worker is still not the production PTZ/cache or media owner. Next is
explicit generation handoff, followed by read-only warmup migration.
Two post-restoration filesystem samples showed positive recording growth for all
three camera directories: sorted byte deltas1048576,3670016,5505024. This checks
continued writes, not browser playback or absence of gaps during recreation.

2026-10-13 follow-up: the [generation lifecycle](account-read-lifecycle.md) now
wraps this worker with offline-tested exclusive handoff and fail-closed physical
cleanup. No production ownership migration yet; read-only warmup integration is next.

The existing disabled worker diagnostic now uses the complete
[guarded preparation adapter](read-preparation-adapter.md), including cache
retirement, bounded DNS and generation ownership. Earlier live evidence above
predates that composition; new-path live validation and production pooling remain
pending. Regular background PTZ warmup is unchanged.
