# Open-channel session checks — 2026-09-24

**Implemented/tested in source, not deployed. Temporary login remains disabled.**

`session_channels.run_guarded` owns a socket operation plus one validity watcher.
After initial route authentication, it re-runs the existing token verifier every
second in a worker thread, with a five-second verification timeout. Invalid tokens,
verification errors or timeouts cancel the operation and close the socket with
1008. Normal completion and parent cancellation reap both tasks. No credentials
are logged or returned. It uses the authoritative existing verifier, not a new
signature parser or a process-local authorization cache.

This is bounded periodic revalidation, **not synchronous/zero-latency revocation**:
normally the next check is within one second plus scheduling/verification time.
A stalled verifier can take the additional five-second timeout. Cancelling a thread
await does not forcibly terminate that thread; DB/service operations must remain
bounded themselves. Transport/driver cleanup can take its existing shutdown budget.

## Integrated channels

- `/api/go2rtc/ws`: cancellation closes upstream and cancels/awaits both relay tasks,
  including when the browser or upstream is idle. Previously cancellation while
  waiting could leave relay tasks alive. No recording producer is stopped.
- `/api/cameras/{id}/intercom/stream`: cancellation triggers the existing worker-stop
  path instead of intentional-stop queue draining. Setup and idle streaming are
  covered. Driver cleanup retains its existing bounded wait; data already sent to
  a camera cannot be recalled.

The current dashboard `player.js` explicitly chooses **MSE**, so video bytes travel
through the guarded WebSocket. An obsolete comment in `live-cameras.js` claimed
WebRTC/MSE negotiation and has been corrected. The vendored player and generic proxy
still allow other clients to negotiate WebRTC; a peer may outlive signaling. This
change does **not** claim to revoke those independent peer connections.

The current verifier accepts primary and legacy cookies only. It can now enforce
their seven-day expiry on an already-open socket, but neither type has a revocation
registry. Logout still clears only the caller's cookie; the channel captures the
cookie used to open it. Temporary-key revocation will require session-to-key linkage
in that shared verifier before it can affect these channels.

## Authorization inventory / next gates

| Surface | Current source behavior | Before enabling temporary login |
| --- | --- | --- |
| Settings and key management | Verified primary session required | Keep primary-only; negative tests with real temporary sessions |
| Camera list/controls/recordings/storage | Authenticated primary or legacy | Decide temporary operation permissions; validate fresh key status on every protected operation |
| Camera registry/discovery/media restart | Ordinary authenticated gate | Explicitly classify administrative mutations instead of accidentally inheriting permissions |
| Provisioning/onboarding/vendor controls | Authentication plus local/trusted-LAN gates | Retain locality checks and explicitly decide temporary-user authority |
| MSE media and live intercom | Open-session revalidation added | Link temporary sessions to fresh persisted key validity; test multiple sockets/devices |
| Independently negotiated WebRTC | Generic proxy still supports signaling | Restrict that transport for temporary sessions or track/terminate peers server-side |
| Recordings/download responses | Authentication at request start | Define and enforce handling of in-flight delivery after invalidation |
| Idle dashboard and audio dialogs | Existing periodic API calls can receive 401 | Explicit invalidation notification/watch and complete UI/media cleanup across tabs |

Login-abuse protection, secure-cookie/proxy policy and old-route CSRF review also
remain activation gates. Do not expose temporary login based only on this watcher.

## Evidence and deployment

41 focused channel/intercom/session-principal tests passed under a 512 MiB address
space limit. New tests cover normal completion, operation/verification failure,
verification timeout, parent cancellation, real signed-cookie expiry while open,
media upstream/relay cleanup, initial unauthenticated rejection and idle audio-worker
termination. All sockets/upstreams/workers are fake; no camera audio/light command
or production stream was used. No production token was created or exposed.

Backend rebuild/recreation is pending. No container was restarted for this step.
See [temporary-key activation gates](temporary-access-keys.md) and
[session principal](session-principal.md).
