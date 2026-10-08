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

## Shared-account correction — 2026-10-08

The initial multi-camera deployment exposed intermittent broker-heartbeat failures:
the first two independently prepared routes failed while the last route remained
usable. A camera-3-only six-pass check succeeded (cold 2049 ms, heartbeats 19–32 ms,
fresh preparation 1995 ms). This pattern is consistent with competing account
certifications; it is not proof of a universal vendor session-count limit.

The PTZ pool now retains **one owned socket per access ID/token pair**, not one
independently certified socket per camera. It retains at most four idle account
routes. Within that channel, `ptz_target` selects only devices present and online
in the authenticated inventory. A new target still requires its own three
correlated identity/model/axis reads. Up to sixteen target profiles are retained
within the route's original twenty-second lifetime. They are keyed by public
camera ID **and native device ID**, reviewed identity/directions and credentials;
identical model names or a changed enrollment never inherit another unit's authority.

Camera switching transfers the single socket owner and advances request sequences;
it does not send movement. Shared-cache callers must explicitly select a target.
Every cached foreground acquisition now checks broker liveness before START; a
failed check closes that stale route and permits fresh preparation, still entirely
before the movement boundary. The earlier 1 ms handoff excluded this extra check
and is **not** a current end-to-end PTZ latency claim.

Native gestures are not queued concurrently, even across cameras. The driver's
reentrant control-channel lock is also used by `run_with_fresh_access`, with a
fixed broker-before-device lock order. PTZ keeps ownership until its motion/release
finishes, but keeps motion **outside** the credential-renewal retry closure. Other
helper users cannot authenticate a competing channel between START and STOP.
This conservative driver-wide exclusion includes refreshed access IDs; long audio
or control operations can delay PTZ or return busy after fifteen seconds. Other
brands are unaffected. Separate app processes/vendor apps are not coordinated by
this in-process lock, so external invalidation remains possible.

The service records prepared-state transitions without tokens or vendor replies;
failures retain fixed reason labels/backoff. No control is enabled merely because
preparation succeeds. Other control transports still have on-demand sessions; their
full pooling and longer authenticated lifetimes remain follow-up work. Single-process
deployment is assumed. Production observation and physical first-click validation
must be recorded separately from synthetic ownership/target-isolation tests.

Shared-channel validation: **177 focused tests passed**, including native binding
changes, independent target validation, stale broker recovery before motion,
cross-thread ownership through motion, lifecycle and logging. Peak 107.4 MiB/no
swap under the 256 MiB/50%-CPU test cap. Ruff and full-app Mypy passed separately.

Deployment checkpoint (2026-10-08): commit `8782a45` passed GitHub CI run
`37724189763`. App build `b-f0275f718943` started at 03:47:06 UTC; go2rtc was
not restarted. All three registered PTZ cameras logged successful preparation.
Through 03:49:22 UTC (multiple renewal cycles), no preparation/heartbeat failure
was observed. This is a short observation window, not a long-running reliability
claim or physical first-click validation. Both containers reported OOMKilled=false;
app/go2rtc memory was approximately 119/334 MiB. No motion command was sent.

Extended check through 03:56:33 UTC: the same three preparation transitions
remain, with no `warm_failed` or preparation-failure logs observed since startup
(approximately nine minutes). The health endpoint remains OK. This still does
not substitute for physical latency or long-duration/credential-expiry validation.
