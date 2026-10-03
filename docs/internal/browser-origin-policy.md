# Browser origin / proxy checkpoint — 2026-09-25

Historical 2026-09-25 checkpoint: implemented in source, then not deployed;
temporary login was still disabled. It was subsequently activated (see README).
No camera operations, production authentication, environment changes or container
restarts were performed. This is an origin-based CSRF boundary, not a token system
or a completed internet-exposure audit.

## Shared policy

`origin_policy.py` is used by login (before body collection), logout, ordinary
authenticated writes, primary management writes, media WebSockets and intercom
WebSockets. Authentication, temporary permissions and local-only guards still apply.
Management retains its additional JSON requirement. Protected HTTP reads and `/api/me`
also enforce the configured target Host, but do not reject a cross-origin read based
on Origin alone; no permissive CORS policy has been added.

For writes/socket handshakes, an Origin must exactly match the normalized target.
No wildcard, suffix match, userinfo or `null` origin. Duplicate Host/Origin/fetch
metadata are rejected. `Sec-Fetch-Site` allows only `same-origin`/`none` when present;
`same-site` is not trusted. If Origin is absent, Referer is checked when supplied.
Otherwise scripts remain compatible without either header, but simple HTML form
media types are rejected. Therefore missing headers do **not** prove a browser
request is same-origin; this is an intentional script-compatibility boundary. Cookie
authentication and route-specific authority are still mandatory. Unsafe HTTP requests
fail with 403/no-store; sockets close with 1008 before acceptance/work.

Explicit origin configuration and source/target comparison follow the
[OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
The no-Origin script fallback is a deliberate compatibility trade-off requiring
browser/proxy validation, not a claim that every legacy browser is covered.

## Operator-only canonical origin

New server-only setting: `DASHBOARD_PUBLIC_ORIGIN` (restart required).

- Empty (default): derive the target from direct transport scheme and Host. Existing
  local HTTP/HTTPS access remains possible. This is not a global Host allowlist or
  DNS-rebinding defense for every unauthenticated/static endpoint.
- Set, for example, to `https://cameras.example.org`: use that exact browser origin
  and require the received Host to match its authority. The proxy must preserve Host.
  Paths, query strings, fragments, credentials and wildcard origins are invalid;
  malformed configuration fails validation. Default ports/case/trailing slash normalize.
  IDN names must use their ASCII representation. Only one origin is accepted.
- Cookies are Secure for a configured HTTPS origin even when the backend hop is HTTP;
  direct HTTPS also always sets Secure. HttpOnly/SameSite=Lax/host-only/path attributes
  remain. Log in again after rollout to receive updated cookie attributes.

The bundled launcher keeps `proxy_headers=False`. Forwarded headers do not define
this origin, the Secure flag, or login-throttle identity. Do not enable unrestricted
proxy-header trust in a custom launcher. Clients behind a proxy still share its
login quota; this step does not introduce trusted per-user forwarded IP handling.

## Proxy migration checklist (not production-homologated)

### Public HTTPS plus direct local access — 2026-10-03

The reported deployment returned 200 for anonymous `/api/me`, but browser-shaped
login returned 403 `Cross-origin request denied` before key verification. The
container had an empty `DASHBOARD_PUBLIC_ORIGIN`; HTTPS termination with an HTTP
backend hop therefore produced a scheme mismatch. An empty JSON login without
browser-origin headers reached request validation (422). No actual key was used
in those probes; they do not validate an individual delegated credential.

Set the exact public HTTPS origin and optionally
`DASHBOARD_ALLOW_LOCAL_ORIGIN=true` (default false). With this opt-in, a different
Host is accepted only for a direct loopback/RFC1918/IPv6 ULA/link-local peer and a
localhost/literal address in those ranges. A non-loopback LAN peer cannot claim
localhost/loopback Host. Forwarded, X-Forwarded-* identity/protocol/port and common
proxy identity headers prevent this exception, even with empty values. DNS names
other than the configured public name and exact localhost are not resolved/trusted.

The direct exception uses its actual HTTP/HTTPS (or WS/WSS) scheme and still
requires an exact Origin/Referer match and existing fetch-metadata checks. The
public proxy path remains pinned and issues Secure cookies. Sessions are host-only:
log in separately on the public name and local address. Revocation, expiry,
delegated permissions and local-only provisioning/controls remain unchanged.
This is not a CORS wildcard, a forwarded-header trust switch or TLS on local HTTP.
Only use local HTTP on a trusted LAN. Proxy infrastructure must preserve the
public Host and backend/media ports must remain restricted.

98 focused tests passed for local/proxy login, temporary permission denial and
revocation, IPv6, hostile/sibling origins, public peers and forwarding spoofing.
Peak memory 73.1 MiB, no swap, under a 256 MiB/50%-CPU cgroup. Real browser and
camera-stream acceptance remain separate from these synthetic tests.

### Local rollout evidence — 2026-10-03

Commit `02de214` passed full CI `37092184405`, including the corrected settings
inventory. A focused combined run passed 118 tests before rollout. Built image
`community-cam-guard-app:dual-origin-02de214` with 512 MiB/no-swap/50%-CPU build
container limits and a separately capped 192 MiB build client. The previous image
was retained as `community-cam-guard-app:before-dual-origin-02de214` for rollback.
Only the app was recreated; go2rtc retained its existing uptime.

Configured the deployment's actual public HTTPS origin plus the local exception
in its ignored `.env`; existing authentication secrets/keys were not changed.
The deployed content-derived build is `b-b93ca4beefe6`. Live checks established:

- Local `/health` and public anonymous `/api/me`: HTTP 200.
- Public same-origin HTTPS login and direct localhost/loopback login with empty
  JSON: HTTP 422 (request validation reached, no longer blocked with 403).
- Public login carrying an unrelated Origin: HTTP 403 as expected.
- Three recorder MP4s open and all three growing over a ten-second observation
  (1,835,008 aggregate bytes added); no recording paths or identifiers retained.
- Native diagnostic remains disabled; no RE route or camera control was invoked.

The first checks during app startup briefly saw connection refusal/502; the
post-start checks above passed. No actual user's credential, cookie or temporary
key was supplied to these live probes. Valid delegated authentication/revocation
was tested in isolated databases; user-browser login/stream acceptance is still
required. This does not certify the entire internet-facing deployment.

### Rollout checklist

1. Terminate valid HTTPS at the proxy; preserve the browser-facing Host and WebSocket
   upgrades/cookies. Configure the exact external origin in the server environment.
2. Restrict the backend port to the proxy/trusted infrastructure. This setting does
   not prove that a request actually passed through TLS, authenticate a proxy or
   encrypt the backend hop. Never expose that hop publicly just because cookies are Secure.
3. Keep internal media ports private. No forwarded-header bypass of local-only
   provisioning/controls/intercom was added. A public hostname still fails those
   local-only guards; the existing explicit remote-BLE exception remains separate.
4. Recreate the backend during a controlled rollout, then validate login/logout,
   settings writes and MSE streaming through the proxy. Verify alternate Host/Origin
   denial and expired/revoked session cleanup without physical camera commands.
5. A pinned origin rejects API access by an alternative LAN IP/localhost Host unless
   the restricted direct-local exception above is explicitly enabled. Use the configured origin or remove the setting and restart to return to
   direct mode. An HTTPS tunnel with a changing hostname requires an operator update.

## Evidence and remaining work

284 focused tests passed across origin configuration, auth/session/key management,
bounded login, synthetic media/intercom, settings and provisioning. Coverage includes
hostile/sibling/opaque origins, wrong ports, spoofed forwarding, duplicate headers,
Host pinning, HTTPS-cookie issuance behind an HTTP test proxy and script/local flows.
An existing fake-WebSocket test now waits for ASGI cleanup before its test harness
cancels the connection, removing a cleanup race without changing production teardown.
Serial test peak 123.1 MiB, no swap, capped at 512 MiB/75% CPU. Mypy passed 207 files;
ruff passed. No real proxy/browser TLS path has been homologated.

Remaining: enumerate legacy GET mutations/exceptional routes, static/secret exposure
audit, real browser/mobile/proxy acceptance, final temporary permissions and login/UI
activation. Shared HTTP authentication dependencies cover current ordinary writes;
new public or custom-auth routes must explicitly adopt this boundary. The roadmap's
broader security work is not marked complete.
