# Finite broker read dispatch — 2026-10-11

Offline composition only; no existing production path imports these components.
No camera access, socket creation, login, worker, movement, retry or capability
change accompanies this step.

## Application waiters

`broker_reads.BrokerReads` owns at most eight pending allowlisted B7 reads with
finite deadlines of at most ten seconds. A positive transport ACK records receipt
but neither frees application capacity nor extends the original deadline. B8 can
arrive before that ACK and completes the request once. Duplicates, wrong device,
session/generation or expired responses cannot complete another waiter.

Completions distinguish camera `response` (with its own error code), negative
`transport_rejected`, `timeout` and `cancelled`. Absent transport receipt stays
unknown rather than inferred from a camera response. Polling/cancellation/close
return events explicitly; there is no retained completion queue or payload history.
The path is stored with the original request, not trusted from unsolicited reports.

## Datagram composition

`broker_read_dispatch.BrokerReadDispatcher` combines those waiters with the
existing bounded broker fragment/ACK owner for one authenticated generation.
Its read-only `prepare` returns the existing codec's encrypted B7 request. The
future socket owner must send it once; ambiguous sends are cancelled, not retried.
`receive` checks generation and uses the broker owner's peer/session/decryption/
fragment validation before matching an application result. ACK construction must
succeed before removing a completed application waiter. Failures invalidate the
composition and explicitly return cancelled waiters in a sanitized exception.

Limits remain finite: fifteen-second account composition, eight reads, ten-second
individual timeouts that cannot outlive the account, 512 input datagrams/1 MiB
and two fragment groups. Polling during silence expires requests and closes the
composition at its lifetime boundary. Physical socket cleanup remains the future
caller's responsibility. Returned payloads belong to the caller and are excluded
from repr; the dispatcher retains neither response history nor callbacks.

Tests use real mode-2 encryption and out-of-order fragments, interleaved requests,
both ACK/response orders, capacity after positive ACKs, exact expiry boundaries,
negative transport/camera results, cancellation, malformed fragments and synthetic
ACK-construction failure. Production PTZ, warmup and RTSP are unchanged.

Next: a bounded socket adapter with a single reader/writer, explicit send-failure
and shutdown semantics, followed by read-only integration behind a controlled
diagnostic. This finite component is not yet a long-lived account manager; no
automatic login/renewal, indefinite lease or native streaming activation is implied.

Follow-up: the [exclusive socket adapter](broker-read-socket.md) is implemented and
offline-tested. It owns a supplied authenticated socket through one bounded batch,
reports I/O/protocol failure separately from camera results and closes every exit.
Reviewed server-side authentication/reservation integration remains pending.
