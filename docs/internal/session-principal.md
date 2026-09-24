# Primary-key session boundary — 2026-09-24

## Implemented contract

Deployed with the [settings UI checkpoint](runtime-settings.md#deployed-checkpoint--2026-09-24).
The rollout discussion below retains the original implementation-stage context.

Successful primary-key login now issues a signed version-1 payload with explicit
`authentication: primary` and a random 128-bit session ID. The request body cannot
choose a role. A frozen `SessionPrincipal` is created only after signature, seven-day
age and exact payload validation. Unknown versions/kinds/fields, malformed IDs,
non-object payloads and cookies larger than 2048 characters fail closed.

`require_auth` and the HTTP/WebSocket-compatible `verify_token` use the same parser.
New `require_primary_session` is a reusable **server-side** management dependency:
401 for absent/invalid sessions, 403 for valid non-primary sessions. This step adds
no management endpoint and changes no existing camera-control route permissions.
In particular, it does not yet establish a restricted temporary-user role.

`GET /api/me` retains `authenticated` and adds `authentication` (`primary`, `legacy`
or null) and `can_manage`. These are safe UI hints, not client authority. It returns
no token, session ID, key or credential. The current frontend can continue reading
only `authenticated`; the later settings UI also consumes `can_manage`.

## Explicit migration

Only the exact old payload `{"ok": true}` is accepted as **legacy**. It retains
existing authenticated access until its original expiry, without extending its
lifetime or silently converting it to primary. Future management gates reject it.
A fresh login with the main key issues a primary session. A payload such as
`{"ok": 1}` or one with additional privilege fields is not accepted, even if signed.

No signing-key rotation, database migration, registry write or automatic global
logout is needed. The main login key remains in environment configuration only.

## Boundaries still pending

The [temporary-key lifecycle foundation](temporary-access-keys.md) now implements
internal generation, verifier storage, absolute expiry and persistent revocation.
It is not connected to this session parser/login, so the limitations below still
apply. Activation requires full HTTP and long-lived-channel enforcement together.

Session IDs are identities only: sessions remain stateless and there is no revocation
store. Logout clears the caller's cookie but a copied cookie remains usable until
expiry. Temporary kinds are rejected until key records, non-recoverable verifiers,
expiry/revocation checks and authorization policy are implemented together. Do not
mint a temporary token by merely changing the payload kind.

Open WebSockets still authenticate at connection establishment; this change does
not add immediate expiry/revocation termination. Login abuse limits, CSRF/origin
policy and secure-cookie deployment policy remain separate pending work. Do not
describe this foundation as a completed internet-exposure security audit.

## Verification and rollout

Tests exercise real ASGI login/me/logout and a **test-only** management route,
primary/legacy permissions, malformed and tampered signed payloads, unknown kinds,
unique session IDs, both formats expiring, injected request claims and the explicit
logout-not-revocation limitation. No production key/cookie is printed or stored in
test artifacts; settings use the existing isolated fixtures.

The subsequent [runtime-settings API](runtime-settings.md) now uses this management
dependency and is now deployed. The statements above about absent
management endpoints describe this session-only checkpoint, not that later step.

No live camera, production login or container restart is required for these tests.
The backend change needs an image rebuild/recreation to become active; it is not
deployed by the frontend bind mount. Runtime settings persistence and its UI were
subsequently implemented/deployed; temporary-key activation must additionally close
the pending security boundaries.
