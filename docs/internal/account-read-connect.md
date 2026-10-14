# Bounded DNS and experimental read-account connection

2026-10-13. Implemented and locally tested; **not activated in production**.

## Resolver boundary

`list_dns.resolve_list_hosts` moves only system hostname resolution into a small
stdlib-only Python child (`-I -S`). It does not move authentication into a detached
thread/process. One process-wide non-queued slot admits a lookup; competing calls
fail busy. No cache, automatic retry or hardcoded vendor IPs were added.

The child receives only the fixed list-service hostname/port, no camera credentials,
application environment or inherited application descriptors. Windows SYSTEMROOT
is the sole retained environment variable. It uses the operating system's NSS/DNS
configuration, preserving hosts-file and resolver behavior. Environment-based DNS
overrides are not inherited. This is process-lifetime isolation, not a sandbox.

After spawn, polling checks cancellation and the original deadline at most every
50ms; lookup time is capped at five seconds. Timeout/cancellation kills the child
and waits at most500ms for exit. Unconfirmed cleanup blocks future lookups rather
than creating more processes. Process creation and OS scheduling themselves are
not hard real-time bounded. Interrupted cleanup also blocks reuse.

Output contains at most16 IPv4 addresses, without logs or raw resolver errors;
the parent validates a maximum1024-byte result and rejects hostnames/IPv6/invalid
structures. The child is trusted bundled code, not an arbitrary command runner.
It intentionally excludes imports of the application and its dependency tree.

## Connection factory

`account_read_connect.open_read_connection` is a trusted factory for the experimental
generation coordinator. It checks exact enrollment/camera/device binding before
resolution, then charges DNS time against the same original20-second connection
deadline. Only afterward does it allocate a fresh UDP socket and authenticate.

An optional internal `resolved_hosts` argument on `obtain_list` and
`open_camera_session` carries validated numeric addresses, so authentication does
not repeat synchronous DNS. Existing callers keep the original behavior: this
change does **not** silently spawn a process on every current PTZ operation.

The factory checks target identity/online status and cancellation/deadline again
before transferring the socket. Failure closes it; successful transfer leaves
closure to the finite worker. It does not renew credentials, allocate a direct
media route, move a camera or send an actuator command.

The caller must retain existing camera/account reservations through factory and
worker cleanup. This helper alone is not a global account owner, and must never
run concurrently with legacy authentication for the same account.

## Evidence and next step

115 focused DNS/factory/session/lifecycle/worker/diagnostic/ownership tests passed
under192MiB memory, zero swap and half-CPU limits; peak89.6MiB. Ruff and targeted
mypy passed. Tests include actual isolated child startup using a numeric test IP
(no external DNS), actual termination/reaping of a sleeping child, synthetic
timeouts/cancellation/cleanup failures, no repeated DNS and original-deadline
preservation through authentication. No real camera/provider traffic or deployment.

Next: connect the factory and coordinator to a server-owned read-only warmup
lifecycle with explicit legacy-login exclusion and shutdown ownership. Keep
production PTZ/RTSP unchanged until that integration and CI are validated; do not
reuse a PTZ socket or treat resolver completion as camera authentication success.

Follow-up: the [native PTZ retirement boundary](ptz-read-handoff.md) now provides
experimental exclusion through physical idle-close completion, including timer
races. Composing it with this factory and the worker remains separate from
activating replacement background preparation.

The [integrated adapter](read-preparation-adapter.md) now uses this factory in the
disabled worker diagnostic. `ReadConnectFailure` distinguishes confirmed broker
cleanup from uncertain close, so ordinary connection failures need not poison the
legacy channel. An unconfirmed resolver child remains independently blocked by
the resolver gate; it never holds broker credentials or authenticates a camera.
