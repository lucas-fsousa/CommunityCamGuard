# Temporary access keys — lifecycle foundation, 2026-09-24

**Lifecycle and primary-only management API implemented; API not yet deployed. No
temporary login or management UI is enabled.** Do not create/share production keys
as usable credentials: they cannot log in. The primary environment key and existing
sessions are unchanged.

## Implemented boundary

- `backend/app/access_keys.py` owns validation, generation, metadata, credential
  verification and current-time validity. `backend/app/db/access_keys.py` owns SQL.
  Both are platform modules, independent of camera models/drivers.
- Each generated credential has a random 128-bit public ID and a separate random
  256-bit secret (`secrets`, never user-chosen). The credential is returned only by
  creation. Its object representation suppresses it; callers must still avoid
  logging/serializing the one-time secret except for the future creation response.
- Only a SHA-256 verifier is stored in `dashboard_access_keys`, alongside ID, label,
  UTC creation/expiry timestamps and optional first revocation timestamp. This
  fast verifier is appropriate for generated high-entropy tokens, **not passwords**.
  No plaintext/encrypted recoverable key is stored, and listings exclude verifiers.
- Creation requires a nonempty label (up to 80 characters) and explicit aware
  ISO datetime strictly in the future. Numeric epochs and naive dates are rejected.
  Expiration is absolute, normalized to UTC, never extended by authentication.
- `authenticate` checks token shape/length, compares verifiers with `compare_digest`
  (including a dummy comparison for unknown IDs) and performs a fresh active-record
  check. This is not a guarantee of constant end-to-end response time.
- `active_key` is a fresh DB check for future *verified-session* references. A public
  key ID is **not** a credential and must never authenticate an unsigned request.
- At `now >= expires_at`, the key is invalid. Revocation is atomic/idempotent,
  retains its first timestamp and cannot be undone by this API. Other keys are
  independent; no process-local validity cache survives a revoke.
- Metadata listing is bounded to 100 records per page, default 50. Storage errors
  propagate, never become successful authentication. Future HTTP boundaries must
  sanitize errors and deny access when the store is unavailable.

The schema is lazily initialized when the internal repository is used, not by
application startup. Tests used throwaway SQLite databases only. No production
schema/key write, main-key rotation, camera access or container restart occurred.

## Activation gates / next implementation order

1. Primary-only key-management API is now implemented (checkpoint below). Decide
   and enforce temporary permissions across existing camera/provisioning/
   administration routes before enabling their sessions.
2. [Signed temporary-session linkage](temporary-sessions.md) now has fresh key checks,
   primary independence and transitional default-deny permissions. Public login is
   still disabled; final operation permissions and UI/channel coverage remain pending.
3. Terminate open WebSockets/media authorization and return idle dashboards to login
   promptly on invalidation. [Open-socket revalidation](session-channels.md) is now
   implemented in source, not deployed; [restricted temporary MSE](temporary-live-media.md)
   is covered, while intercom and independent WebRTC remain denied.
   Denying subsequent HTTP requests alone does not revoke an open media connection.
   [Recording/download guards](recording-session-delivery.md) are now implemented;
   validate real proxy/browser interruption and cover WebRTC media that outlive signaling.
4. [Fixed-memory login pacing](login-abuse-protection.md) is now implemented in source;
   body/resource limits and cookie/proxy/origin policy remain pending. Then enable temporary
   login and a compact localized management section. Validate the full flow before
   claiming usable expiring access. Never rely on browser time for enforcement.

Primary-key logout remains cookie deletion, not server-side session revocation.
This work does not close the separate internet-exposure/security-audit backlog.
Server-side expiration and secure random identifiers follow the
[OWASP session-management guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

## Evidence

`tests/test_access_keys.py` covers secret non-persistence, metadata-only listings,
UTC conversion/exact expiry, validation, malformed/incorrect/unknown credentials,
bounded pagination, independent keys, repeated/concurrent revocation, fresh module
reads, revocation during verification and DB failures. Negative integration checks
prove that generated keys still cannot log in and signed temporary claims remain
unsupported; primary login still works. Together with existing auth/principal
tests, **58 tests passed**, under a 512 MiB address-space / 90-second CPU cap.

No complete session/channel invalidation or physical-browser test is claimed here.

## Management API checkpoint — 2026-09-24

`backend/app/api/access_keys.py` registers GET/POST `/api/access-keys` and POST
`/api/access-keys/{id}/revoke` in the main app. The shared management dependency
requires a verified primary session before storage access. All matched-route
responses (including authorization, validation and handled storage failures) are
no-store. Validation responses do not echo submitted inputs. Response models allow
only public metadata, plus the one-time secret on successful creation. List/create
explicitly report `login_enabled: false`; current login still rejects generated keys.

`management_http.py` shares the existing settings JSON/same-origin write guard.
Origin comparison excludes application root paths and does not read forwarded
headers directly. Writes without Origin remain available to authenticated scripts.
This does not harden older camera/provisioning routes or configure trusted proxies.

UTC expiration normalization now happens before insertion, including rejection of
offset dates that overflow the UTC datetime range; no record is created in that case.
Creation is not idempotent and must not be automatically retried after an uncertain
response. List/revoke the uncertain record before deliberately creating a new one.

`tests/test_access_keys_api.py` exercises real ASGI requests, primary-only gating,
legacy/unknown-kind/expired session denial, schema validation, no input echo,
pagination, UTC metadata, idempotent revocation, cross-origin/content-type rejection,
root-path handling, forwarded-header spoof attempts and sanitized storage failure.
Main-app route registration is checked without starting its lifespan/workers.
**94 focused API/lifecycle/settings tests passed** under a 512 MiB address-space cap.
No production DB mutation, token issuance, camera command or container restart occurred.
Backend rebuild/deployment is still pending; no dashboard control was added.
