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
