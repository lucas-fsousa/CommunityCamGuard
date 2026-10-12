# Bounded native-account admission — 2026-10-12

`broker_mailbox.BrokerMailbox` is driver-local experimental plumbing, not a
production worker or a new dashboard capability. Multiple callers may submit,
cancel and consume allowlisted identity reads; one account worker polls work,
binds wire requests and delivers decoded results. It creates no threads/sockets.

Eight slots cover queued requests, active reads, unconsumed results and consumed
cancellations not yet acknowledged by the worker. Admission fails explicitly when
full; no unbounded queue, eviction, retries or history. Deadlines start at admission
and last at most ten seconds. Lock sections contain bounded state operations only.

Mailbox identifiers and broker sequence numbers are separate namespaces. The
worker must bind the exact generation/device/path/wire ticket before sending;
duplicate live wire tickets and cross-delivery between identical reads are rejected.
A failed bind requires releasing the prepared account reservation, without sending.
One mailbox belongs to one account generation, never shared across account owners.

Cancellation stays visible on every poll until `acknowledge_cancel` is called
**after** the worker releases the account reservation. Reading a cancelled result
does not prematurely free capacity. Late results cannot overwrite cancellation or
timeout. Results are consumed once; close rejects new work and preserves completed
results while cancelling outstanding work. Payload size inherits the bounded
account decoder; raw values are hidden from dataclass representations.

Nineteen focused tests cover concurrent admission, capacity retention, cancellation,
shutdown, timeout, exact response binding and sequence exhaustion. Local pytest
peak80.1MiB/no swap, mypy69.8MiB/no swap; Ruff passed. No camera I/O or deployment.
Next: a single-owner adapter applying this contract to `BrokerAccount`, then
exclusive socket scheduling and account-cache migration. Actions/media are excluded.

## Account adapter

`broker_read_dispatcher.BrokerReadDispatcher` now owns one finite account, one
mailbox and two bounded exact-ticket maps. Only its mailbox is thread-safe;
step/receive/close belong to one worker. It publishes decoded results exclusively
to the mailbox and removes mapping entries on every terminal result/cancellation.
Queue wait consumes the original deadline, not an additional ten-second window.

Preparation failure retires the entire generation and discards the unsent batch;
shutdown preserves completed results and cancels queued/active work. A cancelled
bind releases the prepared reservation without sending. The future socket owner
must check `mailbox.ready` immediately before each write, service maintenance
frames and call step during silence. Cancellation after that final check is
best-effort: a read already entering the socket cannot be recalled. No actions
are admitted. Heartbeat still has its independent reserved slot/shared counter.

The adapter is tested locally with synthetic out-of-order replies, eight active
reads across heartbeat, cancellation/preparation races, queue deadlines, wrong
peer/generation and retirement. No worker/socket/cache integration is enabled yet.
