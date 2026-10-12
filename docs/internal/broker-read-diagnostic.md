# Same-process broker read pilot — 2026-10-12

`diagnostics.yoosee_broker_reads.run_reviewed_broker_reads` prepares a finite
read-only comparison before migrating account ownership. It is an internal
callable with a separately gated internal HTTP trigger. There is **no startup
task, standalone CLI or production activation**.

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
included. Exceptions can still propagate internally; the HTTP boundary sanitizes
expected failures rather than returning their text.

Offline tests cover reservation ownership through cleanup, missing/mismatching
targets, authentication and exchange failures, cancellation before authentication,
exactly-once close and summary redaction. No live camera was contacted for this
implementation. Successful transport receipts alone do not prove an application
response, capability support or persistent session ownership.

## Guarded server trigger

`POST /api/internal/diagnostics/broker-reads` is hidden from OpenAPI and requires:

- `BROKER_READ_DIAGNOSTIC_ENABLED=true` (default false).
- Reviewed server-only `BROKER_READ_DIAGNOSTIC_CAMERA_ID` and
  `BROKER_READ_DIAGNOSTIC_DEVICE_ID`; both default empty.
- A primary-key session, direct loopback peer and existing Host/Origin/CSRF checks.
  Temporary/legacy sessions, LAN peers and forwarding headers are rejected.
- An empty POST, no query parameters or transfer encoding. Operator clients must
  send an explicit same-loopback Origin and non-form Content-Type, as with the
  existing native AV diagnostic. Never print cookies or pass them in shell arguments.

One attempt is claimed atomically per server process before work. Busy targets and
expected failures consume it; no automatic retry. Expected exceptions return safe
409/502 messages, while completed exchanges return the safe summary. **HTTP 200
does not establish that any identity read succeeded**: examine every completion
and the separate failure field. An empty/timeout response is not capability proof.

Locks and single-use state are process-local; use only the existing single-worker
server. Restarting while enabled rearms the experiment. Proxy stripping of forwarding
metadata must not expose this path. Client disconnect does not cancel the bounded
synchronous operation; never retry an uncertain HTTP outcome.

The route is registered and disabled by default. Tests exercise authentication,
proxy/LAN/CSRF rejection, invalid input,
single use including concurrent requests, failure sanitization and settings exposure.

## Camera-3 live validation — 2026-10-12

One invocation inside the actual server returned HTTP 200 with **all three reads
completed by correlated application responses**. Each had `transport_result=0`
and `error_code=0`; `failure=null`, six sent and six received datagrams. Raw values
were deliberately not returned or retained, so this confirms the read transaction
path, not particular model/firmware values or capability support.

Deployed source `5f6942b`, image `371a4315043d`. The legacy Docker build was limited
to 384 MiB, no extra swap and half a CPU; its 4.786-MB context excluded secrets and
runtime data. WSL had approximately 1.5 GiB available, but swap became full during
the procedure; do not infer that the host has capacity for parallel heavy work.
Follow-up build improvement: README copying now follows dependency installation,
so documentation-only changes no longer invalidate that layer. Static Dockerfile
and secret-boundary tests cover this order. No additional build was run to benchmark
the change while host swap remained occupied.
The operator process peaked at 111.4 MiB with no swap and ran for 20.5 seconds;
this is not the server or Docker daemon's peak measurement.

Only the app was recreated to arm and disarm the attempt; go2rtc and unrelated
containers were not restarted. The two recreations briefly interrupted recording;
this is not a zero-gap deployment. Finally restored all diagnostic enables to false,
cleared both reviewed target fields and confirmed health HTTP 200. All three
camera recording totals increased over a subsequent ten-second observation:
262144, 1048576 and 1572864 bytes (sorted, no device identifiers). After restoration,
app/go2rtc usage was approximately 116/25 MiB, each retaining its 1-GiB ceiling.

No camera actions, PTZ, light, siren, microphone or media rendezvous were sent by
this diagnostic. No automatic retry was performed. This is a finite session,
not evidence of persistent heartbeat renewal or simultaneous media/control service.

## Repeating a reviewed check (not an automatic next step)

Review camera 3's current registry/enrollment pair privately, then deploy the tested
app with temporary server-only settings. Account for recording interruption during
app recreation; do not restart unrelated services. Invoke once inside the actual
server through direct loopback, compare the three completion results to existing
correlated identity-read evidence, then restore disabled/empty settings and verify
health/recording progress. Do not use a standalone broker process as a shortcut.

Next work is the single-owner account lifecycle: bounded submission/completion
delivery, sequence allocation shared with heartbeat, generation retirement and
credential-change draining. Do not merely remove the finite dispatcher's deadline
or wire PTZ to its socket. This pilot is not the planned long-lived reader/writer.
