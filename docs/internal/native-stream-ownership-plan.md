# Native stream ownership integration plan — 2026-10-11

Status: code-path audit and proposed migration, not a production source or a new
capability grant. Ten-second camera-3 reception is validated separately in
[duration budgets](native-tcp-duration-budgets.md).

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
PTZ cache lifetimes. The next concrete code unit is the bounded transaction
correlator; its migration must keep the current deployed path unchanged initially.
