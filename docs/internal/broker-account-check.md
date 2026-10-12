# One-socket read/heartbeat validation — 2026-10-12

`broker_account_check.run_owned_account_check` owns one **supplied authenticated**
socket. It sends a validated batch of at most three allowlisted reads, waits for
one scheduled/correlated A0/A1 heartbeat, then repeats the reads on the same account
generation. It neither authenticates again nor borrows the PTZ cache socket.

## Bounds and failure handling

- Sixty seconds after ownership transfer; no retries, workers or media rendezvous.
- At most three read completions per phase and one heartbeat completion retained.
  Raw values remain internal and are stripped by the server diagnostic summary.
- Existing encrypted broker input checks, 4-KiB datagrams, 512-datagram/1-MiB caps,
  two fragment groups and ACK handling. `BrokerMaintenance.account_check` is a fixed
  60-second factory; default native AV limits remain 15/28 seconds.
- Polling during silence, one heartbeat slot reserved from read admission, and
  cancellation checks before writes and after reads. Responses remain visible if
  subsequently sending their ACK fails; that failure is reported separately.
- Every exit closes the account, fragment state and physical socket, including
  invalid input, cancellation, I/O failures and interruptions. A close failure
  cannot turn an otherwise uncertain result into success.

The fake-socket tests use the real encrypted codecs and a fake clock to cross the
50-second initial heartbeat interval without sleeping. They cover sequential
read/heartbeat identities, reliable response ACKs, silence, heartbeat timeout,
read failures, cancellation, interruption and physical close. This validates
software composition, not real relay persistence.

## Opt-in server integration

The existing hidden `POST /api/internal/diagnostics/broker-reads` accepts the new
server-only `BROKER_READ_DIAGNOSTIC_HEARTBEAT=true` (default false). No request field
can enable it. All primary-session, direct-loopback, reviewed-target and one-use
guards remain. Set the three existing diagnostic enable/target fields as usual;
only camera3 is authorized for this research.

Authentication retains its 20-second facade budget, account-lock acquisition can
wait up to15 seconds, and the socket experiment has a separate60-second ceiling.
Operator HTTP timeout must exceed95 seconds; timeout is not permission to retry.
The camera/account reservations span the entire check and cleanup, so other native
controls may report busy during this finite observation. This is intentionally
**not** a production solution for concurrent controls or permanent session ownership.

The safe response preserves `observations` for the initial reads and adds
`after_observations` plus heartbeat status/interval hint. Both read phases must have
three application responses with zero errors and the heartbeat must have status
`response`, with no separate failure. Transport ACKs or HTTP200 alone do not meet
that criterion. An absent transport ACK does not invalidate a correlated application
response, but must remain distinguishable in the observations.

At implementation time this mode is offline-tested, disabled and not yet deployed.
Next: one reviewed camera3 attempt through the actual server, then disarm/clear the
target, verify health and recording growth, and document the result without keys,
identifiers or raw device values. Do not use a standalone shared-account probe.
