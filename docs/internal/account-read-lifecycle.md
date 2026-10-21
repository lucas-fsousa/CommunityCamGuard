# Read-account generation handoff

2026-10-13 — experimental, offline-tested; **not wired into production**.

`account_read_lifecycle.AccountReadLifecycle` wraps the actual finite identity-read
worker with a per-account generation coordinator. It does not implement login,
renewal, a background thread, a new API or a new dashboard capability.

2026-10-20: it also propagates explicit [continuous policy](continuous-account-policy.md)
to the same sole reader. Finite remains the default; physical close/STOP requirements
for replacement are unchanged. No production caller opts in.

## Contract

An explicit `run(connect)` reserves the generation **before** calling the trusted
connection factory. Concurrent/reentrant runs fail without invoking that factory.
The caller must retain existing account/enrollment reservations across the entire
call and must bind this coordinator to one account. Creating several coordinators
does not provide process-wide account exclusion.

The connection factory must have bounded authentication/I/O, clean up its own
resources on failure and transfer a fresh, exclusively owned authenticated socket.
Never pass a borrowed PTZ/cache socket. The coordinator does not impose a timeout
on this synchronous factory; stop/drain during connection latch until it returns.

The normal sequence is `idle → connecting → active → idle`, with optional
`draining` before completion. Returning to idle requires the actual worker summary
to confirm physical socket close. An exception, interruption or close failure
leaves the coordinator blocked; there is no reset/retry bypass. This is deliberately
conservative even when an interrupted worker may have closed successfully.

- No authentication, socket read/write or physical close runs under the lifecycle lock.
- Drain immediately closes admission and cancels queued work. Already-sent reads
  may finish within the worker's existing two-second drain window.
- Stop rejects admission and requests worker termination. Neither operation
  recalls a datagram whose send has already started.
- Generations increase monotonically, without wraparound. Old tickets cannot
  cancel or consume results in a new generation, even with identical sequences.
- Only the latest worker's eight-slot mailbox is retained. Its terminal results
  are available until the next explicit run, then discarded; there is no historical
  queue, command replay or automatic resubmission.
- Worker-construction failures close an already-transferred socket. Once run starts,
  only the worker closes it; the coordinator does not close it a second time.
- Existing identity-read allowlists, budgets and per-camera capability policy remain
  unchanged. Do not use this read-only lifecycle for motion or media teardown.

## Validation

127 focused lifecycle/worker/dispatcher/mailbox/account/diagnostic-adapter tests
passed under a 192 MiB cgroup, zero swap, half-CPU cap; observed peak 85.2 MiB.
Ruff and targeted mypy passed. Tests use real worker encryption/decoding with
synthetic sockets and fake time; a bounded test thread covers concurrent connection
reservation and stop/drain. No camera traffic, deployment or container restart.

The tests cover old-generation isolation, pending-response drain, queued cancellation,
failure during construction/authentication/worker execution, uncertain close,
generation exhaustion and exclusion while physical close is still running.

## Next integration boundary

Move read-only warmup into a server-owned account lifecycle **only after** providing
a bounded authenticated factory and coordinating every legacy account login path.
Preserve the existing enrollment and foreground START/STOP reservations. The
production PTZ cache still owns its reader, heartbeat and timer; none may share a
socket with this worker. Migrate those responsibilities together in a separate
reviewed step, before any native media or motion adoption.

This completes the offline generation-handoff unit, not persistent session pooling,
credential-expiry homologation or WAN-independent authentication.

### Authenticated-factory audit

`camera_session.open_camera_session` calls `access_session.obtain_list`, which
performs synchronous `socket.getaddrinfo` before its deadline-checked UDP loop.
`BudgetSocket` limits UDP operations, but does not bound that resolver call. Thus
wrapping the existing opener in a 20-second socket budget is **not** a hard
20-second factory bound. A thread timeout that merely abandons a still-running
authentication would also violate exclusive ownership and must not be used.

Before background integration, define bounded resolver admission/cancellation,
then ensure authentication has actually terminated and its socket is closed before
another generation starts. Preserve DNS/proxy configuration; do not hardcode vendor
IPs or confuse this issue with the separate LAN-only DNS-redirect investigation.

Follow-up: [bounded DNS and connection factory](account-read-connect.md) now
implements that boundary for the experimental path. Default production callers
remain unchanged; server-owned warmup/account coordination is the next integration.
