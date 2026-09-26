# Login request and cookie checkpoint — 2026-09-25

Implemented/tested in source; backend deployment remains pending. Temporary login
is still disabled. No production logins, camera actions or container restarts.

## Request lifecycle

POST `/api/login` first reserves the existing origin quota. `login_request.py`
then admits at most **8 concurrent login handlers per application instance**, without
queuing excess requests (503 + `Retry-After: 1`). All acquired slots are released on
completion, rejection, exception or cancellation. Saturation can temporarily deny
other origins; this is a short-lived work cap, not a credential/account lockout.

The HTTP body is limited to **16 KiB**, counted from actual received chunks even
without Content-Length or with a false small length. Oversized requests return 413;
malformed/duplicate/mismatched lengths return 400. Collection has one total **5-second**
deadline (408), not a deadline reset for every chunk. Disconnects abort collection.
Encoded/compressed bodies and non-JSON content types are rejected with 415. Missing
Content-Type retains FastAPI's JSON default; application/json and application/*+json
are accepted. Keys are never truncated/normalized: an oversized JSON envelope is
rejected explicitly, including an unusually long primary key configured in `.env`.

Only a bounded body is replayed into FastAPI. Schema/JSON validation failures return
a generic 422 without Pydantic `input` fields that could echo credentials. Responses
from these boundaries carry `Cache-Control: no-store`. The UI distinguishes 408/503
temporary unavailability from invalid credentials and origin throttling (429).

The byte cap bounds application accumulation, not an already allocated ASGI chunk,
HTTP headers, transport/proxy buffers or aggregate connection count. The deadline
covers body collection, not the entire request or a running worker thread. There
is no shared multi-worker/distributed quota. Reverse-proxy/server connection and
header limits remain deployment responsibilities. No claim of DDoS resistance.

## Cookies and origin/proxy review

Follow-up: [shared origin policy](browser-origin-policy.md) now covers authenticated
writes/login/logout/socket handshakes and adds optional canonical HTTPS origin cookie
handling. The observations below record the prior checkpoint; proxy/browser rollout
and exceptional-route review are still pending.

`api/auth.py` now sets `Secure` when the ASGI request scheme is HTTPS; local HTTP
remains supported. Cookies remain host-only, HttpOnly, SameSite=Lax, Path=/ and
seven days; logout deletes with matching security attributes and no-store. Raw
forwarded headers never select this flag. Existing cookies require a fresh login
to obtain updated attributes. Primary logout still does not revoke other copies.

Reviewed boundaries, not claims of a completed security audit:

- `management_http.py` requires JSON/same Origin for key/settings writes, allows
  absent Origin for scripts and rejects cross-site fetch metadata.
- `require_auth` verifies identity/temporary permissions, not a universal CSRF policy.
  Login/logout and legacy control writes still need a coordinated origin/CSRF review.
- The bundled launcher disables forwarded-header rewriting. A TLS-terminating proxy
  therefore presents HTTP to the app unless an explicit trusted-proxy policy is
  implemented. HTTPS outside the proxy alone does **not** activate this Secure flag;
  strict Origin checks may also mismatch. Do not deploy by blindly enabling forwarded
  trust. Host allowlisting, canonical public origin and proxy trust remain pending.
- HTTP LAN cookies are still transport-readable; these changes do not make public
  HTTP exposure safe. Browser-side hiding is never an authorization mechanism.

## Evidence and next step

202 focused tests passed across request boundaries, pacing, sessions, keys, application
and provisioning. Synthetic ASGI tests cover chunk/length limits, stalled body timeout,
busy rejection without reading, disconnect/exception/cancellation cleanup, credential
redaction and HTTPS/HTTP cookie behavior with spoofed forwarded headers. No camera
traffic was generated. Serial test peak: 82.8 MiB, no swap, capped at 512 MiB/75% CPU.
Mypy passed for 206 files; ruff and Node session lifecycle contracts passed.

Next: explicit proxy/public-origin policy and consistent origin/CSRF protection;
then final temporary operation permissions, real browser/proxy validation and public
login/UI rollout. See [activation gates](temporary-access-keys.md).
