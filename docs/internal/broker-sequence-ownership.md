# Account generation sequence ownership — 2026-10-12

The finite read pilot is live-validated, but each transaction registry previously
owned its own sequence counter. Creating separate heartbeat/control registries
from the same certified node would duplicate sequence numbers. Increasing socket
lifetimes would not solve that ownership problem.

`BrokerSequences` now supplies one constant-memory namespace per authenticated
session/generation. `BrokerTransactions` and `BrokerReads` accept an optional
shared namespace, verifying its session and generation before attaching. The
legacy constructor seed initializes a private namespace only when no shared one
is supplied; when supplied, the shared counter is authoritative. Existing finite
callers keep private ownership and the same wire numbering.

- Allocation never wraps or recycles numbers after cancellation, failure or timeout.
- Exhaustion rejects new allocations but permits already-issued replies to finish.
- Invalid or capacity-rejected registrations do not consume a sequence.
- Closing one consumer cancels its waiters, not the shared namespace or other users.
- The account owner explicitly retires the shared namespace before replacing it.
  Retired namespaces reject allocation and prevent both transport and application
  responses from completing old waiters. The owner must still close consumers and
  deliver their explicit cancellations; retirement alone is not socket cleanup.
- Local generation matching does not authenticate the wire or justify reusing old
  session keys. The owner must bind replacement to fresh authenticated state.

This is single-owner code, not a thread-safe allocator or an account manager.
Aggregate queue/pending limits remain a responsibility of the future account
owner; attaching multiple registries must not be used to bypass global limits.
There are no new sockets, workers, timers, heartbeat packets or production controls.

Offline tests cover interleaved readers/receipts/reserved sequence slots, namespace
identity mismatch, retirement, late responses, consumer cleanup, capacity rejection
and uint32 exhaustion. Reserving a slot for heartbeat is **not** proof of heartbeat
response correlation: the existing protocol sends A0 and receives A1, whereas the
transport receipt matcher handles same-kind acknowledgements. The future heartbeat
waiter needs its own verified reply contract and must use the account's sole reader.

Next: bounded heartbeat-response correlation and scheduling, followed by explicit
account lifecycle/draining and bounded submission/completion delivery. Keep the
current PTZ cache/socket handoff unchanged until those boundaries are tested.
