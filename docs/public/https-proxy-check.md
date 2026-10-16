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

## Idle WebSocket authorization checks

From a Python environment with the project's runtime dependencies and the server's
configuration, explicitly run:

```bash
python -m scripts.check_public_channels --camera-id <registered-camera-id>
```

This uses the configured HTTPS/WSS origin and creates four labeled temporary test
keys. It verifies denied video/intercom handshakes, revocation of an accepted idle
video channel while an independent key remains connected, expiration and denied
reconnection. All known test keys are revoked in cleanup; audit rows remain.
It never sends an MSE request or PCM audio, so no upstream video/encoder or camera
audio session is intentionally started. Accepted intercom with permission is not
tested. Unexpected handshake acceptance fails the check immediately.

`--loopback` compares the direct local endpoint (local origin must be allowed).
`--proxy-loopback-port 3080` compares a local nginx listener, preserving the
configured public Host/Origin while bypassing external TLS/tunnel transport for
the diagnostic socket only. These options are diagnostics, not public HTTPS proof.
The camera ID must already be registered. No IDs, cookies or secrets are printed.

The check is bounded and reports unexpected handshakes, missing close frames and
timeouts as failures, not successful revocation. Expiry timing is measured from
socket acceptance, not key creation; login/network latency consumes part of its
five-second lifetime. A successful idle-channel check does not homologate media
delivery, rendered browser frames, active-stream interruption or mobile behavior.

## Revocation during actual media delivery

Explicit opt-in (test camera only):

```bash
python -m scripts.check_public_channels --active-media --camera-id <registered-camera-id>
```

This variant creates three test keys and requests video-only MSE for two sessions
on the same reduced-quality source. It may start one shared on-demand encoder;
check available resources first. No microphone, camera control, saved media or
decoder is used. Run under a bounded cgroup and check producer/consumer cleanup
afterward. Do not run multiple copies concurrently.

The probe requires actual fMP4 media data before revocation, drains bounded bytes
already in flight until close1008, confirms another key still receives media and
answers ping, then verifies denied reconnection. Startup allows15seconds for a
cold encoder; revocation retains a separate7second deadline and16MiB input cap.
It drains video while awaiting pong: waiting for pong with a full bounded receive
queue would falsely report a disconnected independent session.

This mode checks revocation, not expiry during active playback. It verifies media
delivery at the WebSocket boundary, not successful browser decoding or removal of
previously buffered frames. All known test keys are revoked during cleanup.

## Expiry during actual media delivery

```bash
python -m scripts.check_public_channels --active-expiry --camera-id <registered-camera-id>
```

Separate opt-in, mutually exclusive with `--active-media`: creates one live-only
key expiring25seconds after preparation and starts one reduced-quality video-only
consumer. Requires actual fMP4 before expiry, drains the bounded receive queue
while waiting, and requires close1008 within7seconds after scheduled expiry.
Early closure does not count as success. Verifies denied WebSocket reconnection
and HTTP401 with the expired session; revokes the known test key in cleanup.

Timing is measured from scheduled expiry, not socket acceptance. The byte counter
covers the entire drain window (including before expiry), capped at16MiB/1024
frames. No recording, decoding, microphone or camera control. Use the same resource
limits and cleanup checks as the revocation probe. This verifies delivery-level
expiry, not removal of already-buffered browser frames or UI logout behavior.

Public acceptance2026-10-15: camera3 close486ms after expiry, reconnect/HTTP denied,
46.3MiB peak/no swap. Zero source consumers/active producers after cleanup;
three cameras still online and recording. No application rebuild required.
