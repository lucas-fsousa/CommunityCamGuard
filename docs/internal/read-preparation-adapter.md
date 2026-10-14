# Guarded read-preparation integration

2026-10-13 — source-integrated, locally tested; no deployment or camera I/O.

The existing disabled `BROKER_READ_DIAGNOSTIC_WORKER` profile now calls
`read_preparation.prepare_identity_reads` instead of opening its socket through the
legacy unbounded-DNS diagnostic branch. The short/heartbeat profiles are unchanged.
No new endpoint, frontend control, capability or automatic background task was added.
Existing primary/local/reviewed-target/one-use API guards remain in force.
Pending PTZ/warmup rejection (`ControlNotReady`) maps to the existing sanitized
HTTP409 unavailable response and consumes the attempt, not an uncaught500.

## Ownership sequence

1. Server diagnostic retains its camera/enrollment and broker reservations.
2. Adapter verifies the enrolled camera/device and checks cancellation.
3. Native handoff rejects pending PTZ/warmup and retires cached sockets, including
   timer closes already in progress. Its broker reservation spans all later steps.
4. Generation lifecycle claims admission before the bounded DNS/connection factory.
5. Three fixed identity reads run through the actual finite single-reader worker;
   one heartbeat and normal60-second completion remain this diagnostic's criteria.
6. Worker closes its exclusive socket before the adapter reports success or exits
   the legacy exclusion boundary. Only fixed observations/counters escape, not
   identity values, credentials, addresses or a live socket.

`AccountReadLifecycle.run_reads` validates a maximum-eight initial tuple using the
existing mailbox allowlist before authentication. It captures bounded batch results
before releasing generation admission; a later generation cannot erase those
returned results. Existing concurrent submit/cancel/take behavior is unchanged.

## Failure and cancellation

`ReadConnectFailure` is raised only after the connection factory confirms cleanup
of any allocated broker socket, or fails before allocating one. The adapter handles
that failure *inside* the handoff reservation, returning a fixed `connection` result
without poisoning legacy PTZ. A socket-close failure escapes without that marker.
Interruptions/unknown failures remain conservative and block subsequent broker login.

A cancelled worker or failed read with confirmed socket close returns its failure
without poisoning the legacy channel. A failed worker-close summary is explicitly
raised before leaving the handoff scope; it cannot be disguised as a successful
context exit. There is no authentication retry or command replay.

Successful reads require correlated camera responses with application error0.
The transport ACK may not yet have arrived (`transport_result=None`); this is a
legitimate ordering already supported by `BrokerReads`. An ACK without a camera
response, a transport rejection or an application error never counts as success.

## Validation and remaining work

121 focused adapter/lifecycle/factory/cache/worker/ownership/service/diagnostic/API
tests passed under192MiB/no-swap/half-CPU limits, peak104.2MiB. Ruff and targeted
mypy passed. Synthetic tests exercise the actual encryption/decoding worker and
legacy reservation through physical close, cancellation, known/unknown factory
failure, closed read failures, uncertain close, detached results and API dispatch.
No actual camera/provider access, rebuild, service restart or local full suite.

This is a finite guarded preparation **diagnostic**, not production session pooling:
it closes on return and does not make later PTZ clicks reuse the new worker. Do not
replace regular background preparation with a60-second exclusive diagnostic.
Next production step requires typed foreground handoff, reserved STOP capacity,
bounded worker lifetime/shutdown and removal of the competing legacy reader/timer.
Keep deployed PTZ preparation/RTSP unchanged until those responsibilities migrate
together. New-path physical validation remains pending after green CI and a
deliberately bounded deployment; the previous worker-only live check does not
homologate this newly composed path.
