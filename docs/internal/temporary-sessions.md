# Staged temporary sessions — 2026-09-24

**Implemented/tested in source, not deployed. Public temporary login/UI remains
disabled. No production keys or cookies were issued.**

## Binding and validity

Internal `auth.issue_temporary_token` requires the complete generated credential,
verifies its hash/status and signs exactly `v:1`, `authentication:temporary`, random
`sid` and public `key_id`. No role or ID supplied alone can authenticate. Raw keys
and verifiers are absent from cookies. No public route invokes this issuer yet.

The parser verifies signature, seven-day age, exact fields and ID shape before
re-reading persisted key status. No validity cache: every check rejects an expired,
revoked or missing key. All sessions for a revoked key fail their next check, while
primary/legacy sessions remain independent of that database. Storage failures deny
access without exposing internal details. The cookie's seven-day ceiling still
applies when a key expires later; no read renews either deadline.

A revoke concurrent with issuance can leave a signed cookie in the return value,
but that cookie fails its next validity check. Future login must account for this
race. Logout/per-session revocation is not added: clearing the cookie does not
invalidate copies; a temporary key's revocation invalidates all derived cookies.

## Transitional permissions — not the final product role

`session_permissions.py` allows temporary sessions only these GET route templates:

- `/api/cameras`, `/api/cameras/status`
- `/api/recordings`, `/api/recordings/playback-status`
- `/api/storage`, `/api/media/streams`, `/api/media/activity`

The subsequent [guarded delivery checkpoint](recording-session-delivery.md) also
allows GET `/api/recordings/file`, GET `/api/recordings/download` and POST
`/api/recordings/prepare`. File bodies now have temporary-session lifetime guards.

`require_auth` matches the resolved route template and exact method, not URL
prefixes. Other protected operations, including new routes, deny with 403. Settings
and keys retain their separate primary-only gate. Invalid/expired/revoked sessions
receive 401. `/api/me` can describe an internally issued temporary session with
`can_manage:false`, without returning key/session IDs or credentials.

Both WebSocket entry points use `verify_channel_token`: temporary sessions are
rejected before acceptance/work. Do not replace this permission gate with the
identity-only `verify_token`. This keeps uncovered WebRTC/streaming paths closed.
Controls and administration are not enabled for temporary sessions yet; archive
delivery is now covered as noted above. Primary/legacy behavior is unchanged. These are temporary
development safeguards, not a permanent read-only guest-role decision.

## Verification and next steps

141 focused tests passed across sessions, keys/API, principal, channels and intercom
under a 512 MiB address-space cap. Tests cover multiple cookies per key, independent
keys/primary sessions, exact expiry/cookie ceiling, storage outages, malformed signed
claims, privilege escalation, revoke-during-issuance and actual app HTTP/WS denial.
All databases/services are isolated; no workers, cameras or containers were started.

The [dashboard watcher](dashboard-session-watch.md) now implements validity polling,
stale-response rejection and player/audio cleanup. Real multi-tab/device invalidation
remains to be validated. Archive file invalidation is implemented in source, with
proxy/browser validation pending. Before activation: final ordinary-operation permissions;
stream invalidation and constrained or terminated independent peers; prompt dashboard
logout with complete media/dialog cleanup across tabs/devices; login-abuse and
cookie/proxy/origin protections. Only then wire public login, ship management UI,
rebuild and validate end-to-end. See [keys](temporary-access-keys.md) and
[channels](session-channels.md). Backend deployment remains pending.
