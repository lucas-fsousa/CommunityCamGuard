# Login pacing checkpoint — 2026-09-25

Implemented in source; backend deployment remains pending. Temporary public login
is still disabled. This checkpoint does not declare internet exposure safe.

`login_throttle.py` wraps POST `/api/login` before body parsing/key verification.
All admitted attempts count, including malformed input and successful logins.
Each origin has a burst of 10 attempts, replenished at one attempt per 6 seconds
using a monotonic clock and an atomic reservation. Rejected requests do not extend
the wait. HTTP 429 includes integer `Retry-After` and `Cache-Control: no-store`,
does not set a cookie and never inspects a submitted key. Invalid credentials keep
the same 401 message; successful logins cannot reset the quota. Existing sessions,
`/api/me`, logout and other routes are not subject to this login-only limiter.
The dashboard has localized English/Portuguese throttling feedback.

## Bounded state and trade-offs

4096 fixed hashed buckets hold only quota timestamps. Hashing uses a random
process-local secret; there is no credential/IP collection or attacker-driven LRU
eviction. IPv4-mapped addresses normalize to IPv4; IPv6 addresses share a /64 quota.
Missing/unparseable peers share an unknown bucket. Hash collisions deliberately
share quotas, as do clients behind the same NAT/proxy. One origin cannot consume
every bucket, but distributed abuse can still exhaust many buckets. This is not a
global lockout, a distributed limiter or a DDoS defense. State belongs to the app
instance, survives handler regeneration, and resets on process restart. Multiple
workers/replicas have independent budgets; the bundled deployment uses one worker.

## Transport identity

The bundled `python -m backend.app.main` launcher explicitly sets
`proxy_headers=False`. The limiter never directly reads `Forwarded`,
`X-Forwarded-For` or `X-Real-IP`. A custom Uvicorn launcher must use
`--no-proxy-headers` to retain this boundary. A proxy currently becomes the shared
origin: do not enable arbitrary forwarded-header trust to bypass that constraint.

Disabling server-level rewriting also leaves the ASGI scheme as the transport's
actual scheme. TLS-terminating proxies and strict Origin/locality checks need an
explicit trusted-proxy design and end-to-end validation before deployment. This
step has not changed running containers, proxy configuration or camera services.

## Evidence / remaining gates

Tests use synthetic peers/clocks and isolated ASGI applications, not production
brute-force requests. Coverage includes burst/refill, concurrent reservation,
fixed state under many identities, collisions, IPv6 grouping, forwarded-header
rotation, invalid JSON, no cookie on rejection, success without quota reset and
application isolation. Static contracts cover the launcher and localized feedback.
181 focused regression tests passed (authentication, keys, sessions, application
and provisioning). Mypy passed for 205 files, ruff and Node session lifecycle
contracts passed. The serial test cgroup peaked at 85.1 MiB with no swap, capped at
512 MiB/75% CPU. No production login attempts or camera operations were performed.

Request size/time/concurrency limits and transport-based Secure cookies are now
implemented in source; see [request boundaries and remaining review](login-request-boundaries.md).
Still pending: explicit trusted-proxy/cookie policy, CSRF/origin review, final temporary-user
operation permissions, real browser/proxy validation, temporary login/UI activation
and deployment. Login pacing alone does not make weak primary keys safe. Keep a
strong server key, restrict network exposure and do not expose internal media ports.
See [activation gates](temporary-access-keys.md).
