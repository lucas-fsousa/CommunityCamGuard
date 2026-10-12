# Same-process broker read pilot — 2026-10-12

`diagnostics.yoosee_broker_reads.run_reviewed_broker_reads` prepares a finite
read-only comparison before migrating account ownership. It is an internal
callable only: **no HTTP route, startup task, CLI or production activation**.

## Ownership and scope

The trusted caller must run inside the actual server process and supply a reviewed
camera ID and vendor device ID. The diagnostic rejects a mismatching camera,
missing registry entry or mismatching durable enrollment before socket allocation.
It holds the existing camera reservation followed by the shared account control
reservation through authentication, reads and physical socket cleanup. A standalone
process would not share these reservations and must not invoke this pilot.

Authentication uses the existing bounded `open_camera_session` flow and verifies
the returned online device. It does not open A4 media rendezvous, renew credentials,
send actions, probe other cameras or borrow a cached PTZ socket. The authentication
facade has a 20-second deadline; the subsequent exclusive read adapter has its own
15-second ceiling. Reservation acquisition time is separate from those budgets.

The dedicated socket transfers once into the adapter. Exactly three allowlisted
identity reads are prepared with two-second response deadlines:

- `ProConst._productInfo`
- `ProConst._versionInfo`
- `ProReadonly.devInfo`

All requests are prepared before writes; their deadlines are concurrent, not three
sequential two-second allowances. There is no retry or background worker. Cleanup
before ownership transfer belongs to the diagnostic; after transfer it belongs to
the adapter, including interruptions and failures.

## Results and evidence limits

The returned summary contains only path, completion status, transport result,
application error code, sanitized failure category and datagram counters. Raw
identity values, keys, device identifiers and network exception messages are not
included. Exceptions can still propagate internally; a future HTTP boundary must
sanitize them rather than returning their text.

Offline tests cover reservation ownership through cleanup, missing/mismatching
targets, authentication and exchange failures, cancellation before authentication,
exactly-once close and summary redaction. No live camera was contacted for this
implementation. Successful transport receipts alone do not prove an application
response, capability support or persistent session ownership.

## Next

Add a disabled-by-default, primary-authenticated, local, single-use server trigger
with a server-reviewed target, following the existing native AV diagnostic gates.
Then compare one camera-3 run with existing correlated identity-read evidence.
Do not migrate PTZ or introduce sustained native streaming on the basis of these
offline tests. This pilot opens a finite authenticated session; it is not the
planned single long-lived account reader/writer.
