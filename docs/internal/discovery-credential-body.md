# Discovery credentials and access logs — 2026-09-26

Source checkpoint, not deployed. No network scan, production credential, camera
request, historical log inspection or container restart was performed.

## HTTP migration

`POST /api/discovery/scan` now accepts optional JSON containing only `username` and
`password`. Missing body or `{}` preserves the dashboard's current credential-free
Scan Network action. All query parameters, including empty or duplicate credential
parameters, return 400 without invoking discovery; there is no compatibility redirect
or silent fallback to unauthenticated scanning for credential-bearing URLs.

`api/discovery_input.py` authenticates/checks origin before collecting at most 16 KiB
within a total five-second body deadline. Fields are strict strings, limited to 1024
characters; the password is SecretStr until scanner dispatch. Unknown/duplicate fields,
invalid JSON/schema/encoding and null/array bodies return fixed errors without submitted
values. Oversize/timeout/unsupported media have 413/408/415 responses. Content-Length is
checked against actual bytes; errors are no-store. The OpenAPI request schema marks
password write-only and does not advertise query credentials. These per-request bounds
are not a global connection/body-concurrency limit or encryption at rest/in transit.

Legacy API clients must move credentials into the JSON body. Use HTTPS when appropriate
and avoid shell commands, debugging middleware or proxy settings that print bodies.
Credentials may still exist in scanner memory while used; successful driver payloads
and scanner/SDK internal logging are separate audit surfaces.

## Why rejecting query credentials alone is insufficient

A rejected request URL can already be recorded by the server/proxy. The bundled
`python -m backend.app.main` launcher now installs `access_logging.py`, which strips
the entire query string from Uvicorn HTTP access records before formatting. Method,
path, peer and status remain. An unknown record format emits a redacted placeholder;
no original message is retained. This applies to all HTTP endpoints, not only discovery.

This does not erase old logs, strip secrets deliberately put in a URL path, sanitize
all application/SDK logs or configure an external proxy/tunnel. Custom Uvicorn
launchers need the equivalent log configuration (or disabled access logs); proxies
must independently avoid query/body logging. No production log deletion was attempted.

## Evidence and remaining work

93 focused tests passed using fake scanners and an isolated registry. Cases cover
JSON dispatch, unchanged bodyless scans, rejected queries, redacted validation,
body size/deadline/disconnect, auth/origin denial, OpenAPI and compatibility with
Uvicorn's access formatter. Peak 94.3 MiB, no swap, capped at 512 MiB/75% CPU. No
real LAN traffic was generated. Existing DHCP recovery behavior is unchanged.

Remaining: sensitive validation outside login/discovery, remaining provisioning
error projections, successful driver payloads and SDK logs; then browser/proxy
acceptance and controlled deployment. Temporary login remains disabled.
