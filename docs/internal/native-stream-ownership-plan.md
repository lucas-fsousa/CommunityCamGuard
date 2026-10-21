# Native stream ownership integration plan — 2026-10-11

Status: code-path audit and proposed migration, not a production source or a new
capability grant. Ten-second camera-3 reception is validated separately in
[duration budgets](native-tcp-duration-budgets.md).

Latest2026-10-20: [continuous account lifetime](continuous-account-policy.md) is
implemented and offline-tested through the lifecycle/reader. Default diagnostics
remain finite. Next integration is server scheduling/enrollment freshness and media
routes sharing that reader, not another extension of a diagnostic timeout.

First implementation: [bounded transport-receipt correlator](broker-transaction-correlation.md)
is now socket-free and offline-tested, including encrypted fragmented input.
It is not wired into production. Application responses need separate correlation
before building/migrating the single account dispatcher.
The stateless B7/B8 read-response matcher is now separately implemented/tested;
the remaining dispatcher must own outstanding application tickets and deadlines,
not discard them when transport ACKs arrive.
That bounded waiter registry and its finite datagram composition are now implemented
and offline-tested: [read dispatch](broker-read-dispatch.md). Physical socket ownership
and the [read-only pilot](broker-read-diagnostic.md) are now implemented. One in-server
camera-3 run completed all three identity reads with positive transport receipts,
zero application errors and safe cleanup. Existing production controls are unchanged.

## Existing ownership to preserve

| Existing component | Verified responsibility | Integration constraint |
|---|---|---|
| `p2p/camera_session.py` | Discover/certify/init a node, heartbeat, select enrolled online device | Opening a control session is not merely obtaining a cached token |
| `p2p/access_session.py::certify_node` | New random session key and broker session identity | A separate source login is not assumed independent from control authentication |
| `p2p/renewal.py` | Shared broker lock before device lock; at most one stale-access renewal | Never replay media startup or movement inside credential-renewal closure |
| `p2p/ptz_cache.py` | Shared-account key is access identity + credential; socket handoff; 8s idle / 20s absolute lifetime | Timer-driven socket close and warmup replacement cannot own a live source's socket |
| `p2p/ptz_route.py` | Sequence allocation, heartbeat, correlated START/STOP receipts, exclusive socket transfer | Two readers on its UDP socket would steal each other's acknowledgements |
| `services/control_sessions.py` | One bounded worker iterates driver-owned preparation | Keep generic service free of Yoosee session/packet details |
| `diagnostics/yoosee_av.py` | Camera and broker reservations span the whole finite experiment | Suitable for diagnostics, not an indefinite production lock |

The code proves these ownership overlaps. The old failed standalone release and
successful isolated release support session interference as a hypothesis; they do
not establish exact server-side replacement semantics for every model/account.

## Proposed order, without breaking current PTZ

1. Extract a socket-free broker transaction correlator within the Yoosee driver.
   Test session/sequence/type/result matching, fragmented input, bounded pending
   transactions, duplicate handling, sequence wrap and deadline expiration.
   It must not dispatch arbitrary device commands or allocate sockets.
2. Introduce a single driver-owned reader/writer for an authenticated account
   generation. It owns UDP, fragment assembly, sequence allocation and heartbeats;
   consumers receive correlated results, never access the socket directly.
   Keep credential material private and invalidate old-generation callbacks.
3. Migrate read-only preparation first behind tests; then migrate PTZ while
   preserving identity checks, mandatory release and no replay of ambiguous START.
   Do not simultaneously leave the PTZ cache timer and new owner closing one socket.
4. Attach one media-link owner per stable camera ID to that account owner. Distinct
   camera routes retain separate link IDs, TCP channels and exactly-once cleanup.
   Credential refresh must explicitly drain/invalidate affected routes; silently
   recertifying under an active source is not an acceptable recovery strategy.
5. Only then connect the generic media-source lifecycle: one camera ingress,
   bounded per-consumer delivery, defined slow-consumer/drop policy, source failure
   propagation, RTSP fallback and browser codec support. The generic core sees
   opaque camera identity and driver capability evidence, never vendor tokens.

No assumption of indefinite token or socket validity. Account ownership is scoped
to credential generation, while feature support stays per model/device. There is
no dashboard checkbox that can bypass a missing driver capability.

## Acceptance before production wiring

- Synthetic concurrent transactions never steal each other's responses; malformed,
  wrong-session and old-generation messages never complete a waiter.
- Memory, transaction count, queues and pending fragments remain bounded during
  silence, duplicates and a slow consumer. No per-frame thread/process creation.
- Active camera-3 media plus a read-only control uses one account owner without a
  second certification. Movement/light/sound require separately announced tests.
- Source shutdown drains media and broker receipts without cancelling unrelated
  controls; uncertain cleanup is reported and is not retried as a new action.
- Established PTZ and RTSP/recorders retain regression coverage. Broader model
  rollout requires evidence from the relevant driver, not camera number rules.

This plan deliberately does not implement a placeholder driver/source or lengthen
PTZ cache lifetimes. The finite correlator/dispatcher/read pilot are completed;
the next unit is the persistent account lifecycle with bounded submissions and
completions, shared heartbeat sequence allocation and explicit generation retirement.
Its migration must keep the current deployed PTZ path unchanged initially.
The [sequence namespace](broker-sequence-ownership.md) is now extracted and tested:
read/receipt consumers can share account allocation without duplicate numbers;
retirement blocks new work and late completions. Heartbeat A0/A1 correlation,
global mailbox bounds and account draining are now implemented. The same-socket
camera3 read/heartbeat/read check passed; the later concurrent
[single-reader worker](account-read-worker.md) is offline-tested, not deployed.

## Migration audit after the finite worker

Rechecked production `native_ptz`, `ptz_cache`, `ptz_route`, `renewal` and
`control_ownership` against the new worker:

1. `NativePtzRoute.confirm_release` and `heartbeat_node` still read their socket
   synchronously. Neither can run alongside the new reader. The PTZ adapter must
   use correlated owner requests/results before sharing any physical socket.
2. `PtzRouteCache` owns idle-close timers, lease return and target-switch handoff.
   Warmup's15-second replacement and20-second lifetime are not credential TTLs.
   A migration must remove both timer ownership and direct reads for migrated
   accounts; simply retaining the current cached socket longer is insufficient.
3. `own_control_channel` protects the START/release boundary; renewal nests the
   reentrant broker lock before its per-device session lock. A persistent worker
   must not hold that operation lock indefinitely or release it on another thread.
   The future account-owner reservation must coordinate all login paths, including
   warmup and diagnostics, before replacing this protection.
4. Credential replacement must first reject admission, drain old work, finish any
   required PTZ/media release, and confirm old physical socket closure. The new
   worker already reports close failure independently; do not authenticate a
   replacement account while cleanup is uncertain. Never replay a START whose
   send outcome is unknown, even when a token was successfully renewed.
5. The worker mailbox only permits identity-read paths. Extending this to other
   reads requires explicit allowlist/schema tests. PTZ is a separate typed command
   contract with reserved release capacity, not an arbitrary command queue.

Next implementation should validate the finite worker under the existing guarded
in-server enrollment/account reservation, then introduce explicit generation
handoff with failure tests. Read-only warmup migrates before motion/media. No
additional setting, capability flag, cache lifetime extension or production reader
was enabled by this audit. The current dashboard/recording path is unchanged.

The guarded worker check subsequently passed on camera3; see
[live evidence](account-read-worker.md). Generation handoff is now the next step,
not another repeat of the same finite probe. Production migration constraints above
remain unchanged; a successful heartbeat does not establish indefinite token TTL.

2026-10-13: the offline [generation coordinator](account-read-lifecycle.md) now
reserves before the authenticated factory, closes admission on drain and blocks
replacement until physical close is confirmed. It creates no thread and is not a
process-global account registry. Next is bounded factory/server-owned read-only
warmup integration with legacy login exclusion, not another finite camera probe.
