# Proxy trust and malformed-header rejection — 2026-10-16

## Implemented correction

The local-only HTTP/WebSocket guard parsed untrusted Host/Origin/Referer/forwarding
metadata using `urlsplit` without handling malformed authorities (for example,
unmatched IPv6 brackets). The opted-in remote BLE guard had the same issue when
parsing its source URL. Ten isolated ASGI regressions reproduced HTTP500 rather
than the intended403. No authentication bypass was demonstrated.

The hostname helper now returns an unusable empty hostname on `ValueError` so
the existing guard denies access. Remote BLE source parsing likewise produces its
existing generic403. Invalid metadata is never echoed and the protected handler
is not called. Valid LAN/IPv6/tunnel behavior is unchanged; no new trust rule,
driver capability or camera operation was introduced.

105 focused tests passed (local malformed headers, provisioning, origin policy,
login throttle), peak133.9MiB/no swap in a192MiB/50%CPU cgroup. Ruff passed.
Built image `13fa9ca7912f` with512MiB memory/no additional swap/50%CPU builder
limits and a5.216MB context; recreated only the app. No go2rtc/camera restart.

## Deployed forwarding policy

Read the actual external nginx CCG virtual host and shared proxy snippet without
changing them. nginx forwards Host and appends forwarding metadata to the upstream
at127.0.0.1:3200. Verified the running container uses the bundled Python-module
entrypoint and its main/throttle/request/origin/local-guard files match reviewed
source hashes. That launcher sets `proxy_headers=False`.

Therefore forwarded headers are **not trusted client identity** for login pacing.
The limiter uses the transport peer and the shared nginx loopback peer shares one
quota across proxied users. Header rotation cannot select another bucket through
these headers; synthetic route tests exercise the rotation policy. This deliberately
does not implement trusted remote-IP extraction. Shared quotas can throttle
legitimate users behind the same proxy. Quotas reset on app restart and are neither
distributed nor a DDoS protection service. No production quota-exhaustion or
brute-force test was performed.

Public HTTPS cookies/origin checks use the configured canonical origin, not the
untrusted forwarded scheme. Direct LAN access remains a separate narrow policy.
Do not enable Uvicorn proxy-header rewriting to remove shared quotas: that would
change this boundary and requires a separate reviewed trusted-proxy design.

## Actual request checks after deployment

- Anonymous `/api/settings` with forged loopback Forwarded/X-Forwarded-For/
  X-Real-IP remained401.
- Authenticated public access to a local-only read route remained403 with those
  headers. Used a deliberately nonexistent camera ID so even a guard regression
  could not dispatch to a physical camera.
- Malformed Origin and Referer on the read-only provisioning-status route
  returned403 both through public HTTPS and direct loopback.
- Valid primary login still supplied Secure/HttpOnly/SameSite=Lax cookies.
- All three cameras remained online/recording; app and relay running/no OOM.

Final operator probe43.5MiB/no swap. Only normal valid login and bounded GETs;
no control writes, scanning, provisioning changes or temporary key creation.
The local diagnostic is retained in ignored `temp/proxy_boundary_check.py`.
Early probe assumptions were corrected: an injected CF-Connecting-IP returned403
(denial layer not established), and the control root was not a registered GET
route. The final probe uses the actual read route; neither earlier failure is
reported as a product defect or authentication bypass.

This completes the defined deployment forwarding/cookie policy check, not a
blanket internet-safety audit. Keep reviewing new routes, resource limits and
physical-mobile behavior separately.
