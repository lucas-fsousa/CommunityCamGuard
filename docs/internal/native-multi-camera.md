# Native multi-camera ownership

## Implementation checkpoint — 2026-11-06

Production admission remains **one native camera**. Cameras 1/2 are still legacy
RTSP; no live multi-camera migration or extra SDK login has been attempted.

The broker now has a bounded `AccountMediaRoutes` owner, used by `BrokerAccount`
and its dispatcher. Explicit internal `media_capacity=3` allows three different
devices under the same certified account and sequence allocator. Default capacity
is still one. This is not a capability grant or a public configuration switch.

Implemented and tested offline:

- Each device has its own OPEN, relay correlation and CLOSE receipt.
- Account-level receive dispatches a validated broker frame to its bounded routes;
  the worker no longer separately dispatches that same frame to its initial route.
- Link IDs are allocated without reuse within an account generation, using two
  scalars, not a growing history. Late A3 replies cannot select a replacement's
  relay. Exhaustion fails closed before another OPEN; it does not wrap/reuse IDs.
- Clean/unopened terminal routes release their slots. Duplicate devices and
  capacity overflow fail before OPEN. Uncertain broker release fences the account
  and cancels the other routes, preserving the existing retirement rule.
- All routes must share one output budget. Each has an independent OPEN/CLOSE
  reserve; ordinary traffic cannot borrow another route's, PTZ's or siren's
  cleanup allowance. Traffic burst/rate limits remain unchanged.
- Drain/retire cancels every route. Drain waits for every CLOSE receipt; a receipt
  for one route cannot release another. Account-owned certification is dropped
  on retirement; unfinished jobs retain only their existing cleanup ownership.

Targeted first regression run: 110 passed, peak 102.3 MiB; mypy passed the five
changed source modules, peak 127.2 MiB. Both ran with CPU50% and memory limits.
These are simulated broker tests, **not** concurrent real-camera acceptance.

## Remaining delivery steps

1. Extend the account worker to pump per-device TCP channels/deliveries while
   retaining one UDP reader and bounded scheduling/cleanup.
2. Connect per-camera prepared sources to a driver-owned shared account lease;
   match enrollment/account identity explicitly. Do not turn the generic source
   registry into a vendor-specific singleton or raise its capacity independently.
3. Offline admission, failure isolation, shutdown and consumer tests; then deploy
   on camera3 only. Additional real-camera migration needs explicit scope.

## Separate endurance finding

The previous 900-second MSE acceptance remains valid for that interval, not for
the whole night. The persisted log contains a later consumer `progress_timeout`
at 2026-11-06T02:36:21Z followed by a busy retry. At 04:01Z the source API reported
native/active144 and Docker reported OOM=false/restarts=0. API state alone is not
proof of media progress. Preserve this finding; do not mark prolonged endurance
complete from the earlier 15-minute result.
