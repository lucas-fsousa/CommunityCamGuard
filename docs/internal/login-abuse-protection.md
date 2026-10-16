# Login pacing checkpoint — 2026-09-25

Implementation is deployed, including temporary login. The actual forwarding
boundary was verified on2026-10-16; see [deployment evidence](proxy-trust-acceptance.md).
This checkpoint does not declare internet exposure safe. Historical rollout gates
below are superseded by that evidence and the delegated-access activation record.

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

Follow-up: optional [canonical browser origin](browser-origin-policy.md) handles
HTTPS proxy origins/cookies without changing the peer-based limiter identity.

The bundled `python -m backend.app.main` launcher explicitly sets
`proxy_headers=False`. The limiter never directly reads `Forwarded`,
`X-Forwarded-For` or `X-Real-IP`. A custom Uvicorn launcher must use
`--no-proxy-headers` to retain this boundary. A proxy currently becomes the shared
origin: do not enable arbitrary forwarded-header trust to bypass that constraint.

Disabling server-level rewriting leaves the ASGI scheme as the transport's actual
scheme. The configured canonical public origin handles HTTPS cookies/origin checks
without trusting forwarded identity; this deployment policy is now validated.
Remote-IP attribution would require a separate design, not a configuration shortcut.

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
Those rollout gates now have deployed HTTP/browser/channel/restart evidence and
explicit proxy policy; physical mobile and ongoing new-route review remain.
Login pacing alone does not make weak primary keys safe. Keep a
strong server key, restrict network exposure and do not expose internal media ports.
See [activation gates](temporary-access-keys.md).
