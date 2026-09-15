# PTZ credential renewal — 2026-09-15

Native PTZ now wraps **route acquisition/preparation only** in the existing
`run_with_fresh_access` helper. Explicit INITINFO rejection `0x216B` permits one
credential refresh/retry; other errors do not trigger account refresh. A concurrent
operation's already refreshed credentials are reused without another cloud request.

Each preparation attempt validates camera ID and reviewed device identity, checks
gesture cancellation and builds the cache key from current access ID, access token
and device token, alongside camera/profile/directions. The new route is never stored
under the stale credentials. Existing idle entries remain capped and expire normally.

The helper's per-device lock serializes preparation with other helper users. Existing
lock/HTTP timeouts apply; the 12-second preparation budget is per attempt, not an
end-to-end renewal latency promise. Renewal failure can still use the existing
pre-movement fallback unless cancelled. A changed camera binding fails closed.

**`motion.run`, START, release and STOP are outside the retry closure.** No exception
after this boundary initiates renewal or fallback. Interrupted gestures are not
replayed after renewal. No heartbeat, keepalive movement, background login refresh
or indefinite route lifetime was added.

The encrypted account credential and the live route are different resources. Route
cache limits remain eight seconds idle / twenty seconds absolute, with at most four
idle routes. Vendor `expireTime` interpretation/fixed credential lifetime is not yet
established. Raising socket lifetimes requires separate camera-3 resource/STOP proof.

Validation: 135 focused tests across PTZ integration, preparation, cache, motion,
route, protocol, standard controls and credential renewal passed with a 512 MiB
address-space cap. New scenarios cover fresh cache keys, second stale rejection,
cancellation during renewal, incorrect refreshed camera binding, non-expiry rejection
and no retry after the motion boundary. Ruff, Mypy and frontend toast/panel/PTZ
contracts passed. No real camera movement or forced token invalidation was performed;
real expiration/renewal remains a field-validation item. The full Python suite's
previous native crash was not retried as part of this scoped change.
