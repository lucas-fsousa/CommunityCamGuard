# Checking an HTTPS reverse proxy

Configure `DASHBOARD_PUBLIC_ORIGIN` with the actual HTTPS dashboard origin.
To retain direct localhost/private-LAN access, also enable
`DASHBOARD_ALLOW_LOCAL_ORIGIN`. Do not expose go2rtc's internal ports or disable
origin checks to work around a proxy error.

## Avoid redirects on the recordings API

The recordings list is `/api/recordings`, without a trailing slash. An nginx
`location /api/recordings/` with `proxy_pass` can automatically redirect the
slashless request. Behind TLS termination this may expose an internal HTTP port
in `Location`, breaking the browser request before authentication reaches CCG.

If you specialize the recordings prefix, add an exact location as well. Within
your existing server block (using its existing trusted headers/auth policy):

```nginx
location = /api/recordings {
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:3200;
}
```

Keep any required deployment-specific proxy headers in this location too. Do not
strip `/api` or redirect to a filesystem directory. Test the **actual loaded
nginx configuration** before a graceful reload; `nginx -t` without `-c` may inspect
the wrong file when the container uses a custom configuration.

## Repeatable checks

From the repository root:

```bash
docker exec -i ccg-app python - --public < scripts/check_delegated_access.py
```

This explicitly contacts only the configured HTTPS origin, verifies TLS, refuses
redirects, and uses the server key internally without printing it. It checks
anonymous API denial, selected sensitive paths, secure owner-session cookie flags,
cross-origin rejection and aggregate online/recording status. It does not run
camera controls, open streams or convert recordings.

To additionally test delegated login, permission denials, expiration and revocation:

```bash
docker exec -i ccg-app python - --public --exercise-access-keys < scripts/check_delegated_access.py
```

This creates labeled test keys and revokes known IDs in `finally`; revoked audit
rows remain. If creation succeeds but its reply is lost, the diagnostic reports
an unknown outcome requiring owner review. Never blindly retry key creation or
bulk-revoke matching labels. Repeated login tests share production pacing limits:
do not loop this check or disable throttling to make it pass.

Omit `--public` to test loopback. These HTTP checks do not certify browser
WebSocket playback, multi-tab revocation, physical mobile behavior or general
Internet security; validate those separately.
