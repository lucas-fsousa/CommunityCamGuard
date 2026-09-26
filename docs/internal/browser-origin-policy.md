# Browser origin / proxy checkpoint — 2026-09-25

Implemented in source; not deployed. Public temporary login remains disabled.
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
5. A pinned origin intentionally rejects API access by an alternative LAN IP/localhost
   Host. Use the configured origin or remove the setting and restart to return to
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
