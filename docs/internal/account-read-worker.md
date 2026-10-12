# Exclusive account read worker — 2026-10-12

`account_read_worker.AccountReadWorker` composes the bounded mailbox/account
dispatcher with encrypted broker maintenance. It **creates no thread**, performs
no authentication and is not wired into production controls or media. A caller
must own the shared-account/enrollment reservations and transfer an exclusive
authenticated socket to `run`; never lend a cached PTZ socket.

## Ownership and bounds

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
