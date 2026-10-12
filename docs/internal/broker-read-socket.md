# Exclusive finite read socket adapter — 2026-10-12

`broker_read_socket.run_owned_reads` now composes the finite read dispatcher with
one **supplied, already authenticated** UDP socket. Ownership transfers to this
function: the socket closes on success, invalid input, timeout, cancellation,
protocol/I/O failure and BaseException. It never opens/authenticates another
socket, starts a worker, renews credentials, sends a camera action or caches a lease.

The caller must hold the account/camera reservations and verify enrollment/device
bindings before transferring ownership. There must be no second socket reader or
timer closing the same socket. Production PTZ and warmup do not use this adapter.

## Execution contract

- One tuple containing 1–8 allowlisted `ReadSpec` requests; all are prepared and
  validated before any network write. Pending deadlines remain <=10 seconds.
- Sends each prepared request once in input order. Polls before each write and
  skips requests that expired during earlier writes. Partial/ambiguous sends are
  failures, never retry triggers.
- Receives at most 4 KiB per datagram with short waits; the underlying dispatcher
  retains its 15-second, 512-datagram, 1-MiB and two-fragment-group limits.
- Polls request expiry during silence; checks cancellation/deadline after a read
  before accepting its result. Duplicate ACK traffic cannot keep a read alive.
- Returns bounded arrival-order completions plus a separate sanitized failure
  category and attempted-send/received-datagram counts. A send timeout is a socket
  failure, not evidence that the overall experiment deadline expired.
- A valid camera response stays in the result if writing its ACK subsequently
  fails; the separate failure remains visible. Other pending requests are cancelled.
  Successful responses likewise do not hide a failure closing the physical socket.

No payload/error strings from network exceptions are logged or returned. Up to
eight bounded response values may belong to the caller; there is no unbounded
completion queue. Returned prepared wire bytes must not be independently replayed.

## Offline validation and next integration

Tests exercise real encrypted wire codecs through a fake socket: reversed reply
order, exactly-once request writes, positive-ACK-only traffic, silence, partial and
failed writes, read failure, malformed fragments, ACK-send failure, close failure,
cancellation before write/after read, skipped expired unsent reads, batch bounds
and BaseException cleanup. No actual network or sleeps are used by these tests.

The [internal pilot](broker-read-diagnostic.md) now composes authentication and
both reservations, with a disabled-by-default guarded server trigger. Next
make a single camera-3 comparison against existing correlated
identity reads. Do not call it
from a standalone shared-account CLI. This is finite read-only plumbing, not yet
a long-lived session manager or a native streaming capability.
