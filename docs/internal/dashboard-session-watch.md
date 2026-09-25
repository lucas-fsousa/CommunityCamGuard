# Dashboard session watch and cleanup — 2026-09-25

The frontend now checks `/api/me` with `cache:no-store` immediately after login,
then one second after each completed check; focus/pageshow request another check
without overlapping a pending one. Each request has a five-second abort deadline.
An authoritative `authenticated:false` or the existing API 401 path returns to
login. Transient transport errors retry, not assert that credentials are invalid.
The backend also sets `Cache-Control:no-store` on `/api/me` (needs backend rebuild).

This is a UI aid, not security enforcement or a zero-latency guarantee. Sleeping
or background-throttled tabs can delay checks until resumed. Each open tab checks
independently; no credentials are broadcast or stored in browser storage. Server
HTTP checks and socket guards remain authoritative for access.

## Lifecycle boundaries

- The dedicated `session-watch.js` owns its timer, request and focus/pageshow
  listeners. Stop/restart aborts old requests and ignores stale completion.
- Core API calls capture a session generation and discard responses from an ended
  session, including an old 401 that could otherwise log out a subsequent login.
  Dashboard boot/camera reload also prevent stale results from remounting players
  or restarting polling. The thin `app.js` stays below 200 lines.
- Returning to login stops existing live players, recording playback and settings
  requests/timers. `endSession` clears management hints and invokes registered
  cleanup callbacks independently, even if another callback fails.
- Camera controls panel, voice-message and push-to-talk dialogs register cleanup.
  The panel restores inert/scroll state. Voice-message cleanup aborts its HTTP
  upload, stops preview/recording and removes the dialog. PTT force-cancels its
  socket/tracks/context rather than waiting for graceful audio delivery.
- A microphone permission prompt or worklet initialization can complete after
  logout. Both audio paths now check cancellation after awaited startup stages and
  stop late tracks instead of opening/restarting capture. Pending PTT startup and
  normal-stop teardown retain ownership until cleanup completes.

Aborting an upload does not recall audio already accepted by the server/camera.
This does not add server-side logout revocation for primary cookies, revoke an
already-authorized HTTP file delivery or complete every onboarding/schedule dialog
lifecycle. Those paths remain separate review items before temporary access rollout.

## Evidence and deployment

All four Node suites passed. New lifecycle contracts cover polling/non-overlap,
invalidation, stop/restart races, timeout/transient retry, old response/401 rejection,
independent cleanup and late microphone grants. The existing DOM harness additionally
opens both audio dialogs and verifies session cleanup removes them. The lifecycle
suite is now the seventh CI gate; contributor test commands are updated.

74 focused Python tests passed (frontend build, app, principal and staged sessions)
in a 512 MiB/no-swap cgroup; peak 106.9 MiB. No real microphone, camera, stream or
production token was used. Full browser/multi-device/end-to-end invalidation remains
unverified. Frontend bind mounts pick this up on reload; no container was restarted.
The `/api/me` header update and previous auth backend changes need rebuild.

Temporary-key login remains disabled. Next: final operation permissions and server
in-flight stream/file invalidation, login abuse protection, compact management UI
and full rollout validation. See [staged sessions](temporary-sessions.md).
