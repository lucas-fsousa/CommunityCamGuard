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

## Background preparation — 2026-10-04

The persistent account token was **not** regenerated on every click. Cold PTZ
latency came from the short-lived broker route plus three correlated identity/axis
reads. The previous paragraphs describe the original on-demand implementation;
the following changes add read-only preparation without extending route lifetime.

`ControlSessions` now calls the optional driver `maintain_control_session` hook
sequentially, with one worker targeting three-second passes (at least one second
of rest after slow preparation, no catch-up burst). Yoosee only
prepares enrolled cameras with explicit per-camera PTZ capability and a selected
native profile. Other drivers default to no I/O; the generic service contains no
vendor protocol, credentials or capability overrides.

The cache accepts **never-started** routes as well as confirmed-stopped routes.
While idle, preparation confirms broker liveness with the existing A0/A1 heartbeat
(0.75s timeout), allocating a new sequence before sending. No camera START, STOP,
light, sound, direct-media rendezvous or new RTSP connection is sent by the worker.
The heartbeat uses the existing broker verification, not new proof of camera
reachability or authority. Identity/axis evidence still comes from correlated reads.
Socket blocking mode is restored before ownership returns to the PTZ cache.

Eight-second idle cleanup and twenty-second absolute expiry remain. Warm routes
are replaced when at least fifteen seconds old, with fresh identity/axis reads;
idle renewal alone never grants indefinite validity. At most four idle sockets
are cached. This intentionally trades bounded periodic broker traffic for lower
first-click latency; it is not a proven long-lived/zero-network-cost session.
Networks with many cameras, slow preparation or outages cannot guarantee every
camera is ready at every instant. Increasing these lifetimes remains pending.

An active gesture skips warming; a click arriving during preparation joins it
for at most fifteen seconds rather than opening a duplicate session. STOP still
cancels the gesture, and movement remains outside every renewal/retry closure.
Failed maintenance backs off per camera from 30 to 300 seconds. Worker shutdown
cleans idle routes after the current bounded operation; active gestures retain
their own cleanup. Cancelled expiry timers cannot close a newly maintained owner.

On explicit stale-access rejection, the common renewal helper first adopts an
account token already renewed by another camera, avoiding redundant account
refreshes. This benefits all helper users, but **other control transports are not
yet session-pooled**. No guessed token expiry interval or credentials are exposed
to the frontend. Actual account-expiration homologation is still pending.

Enabled with autostart and `CONTROL_SESSION_WARMUP=true` (default); set false and
restart the app to restore on-demand preparation. This is server-only configuration.
148 focused tests passed (92.1 MiB peak, no swap, 256 MiB/50%-CPU cap), including
pristine handoff, heartbeat failure, sequence allocation, no uncertain-motion reuse,
background exclusion/join, backoff and cross-camera account-token reuse. Full-app
Mypy passed under a separate 384 MiB cap. Physical PTZ latency remains a user check.

Read-only camera-3 measurement in an isolated 256 MiB/no-swap/half-CPU container:
cold preparation **2762 ms**, broker keepalive **38 ms**, cached route handoff
**1 ms**. The handoff selected another previously verified direction but sent
**no START or STOP**; the unused lease and all idle sockets were closed. These
numbers establish setup savings, not physical movement latency or a latency SLA.
