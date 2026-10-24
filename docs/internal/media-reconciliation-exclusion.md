# Hub reconciliation exclusion and deferred application — 2026-10-24

Status: integrated and tested with fake services/loopback fixtures; not deployed.
No hub, production container or camera was restarted by this work.

## Shared synchronous mutation boundary

`media/reconciliation.py` supplies a reentrant guard with a finite acquisition
timeout (default5s, maximum30s). The timeout limits waiting to **enter**, not the
duration of the contained operation. There is no thread, queue or global singleton.
Use it on service/coordinator threads, never across ASGI awaits.

Go2rtc config writes, start/stop, external reload, full restart/config application
and live-producer release acquire this guard. Composite operations hold it across
the whole write/apply sequence, not just individual subcalls. Reentrancy allows a
coordinator's publication callback to write config under the same owner thread.
Read-only liveness/health queries and existing streaming pumps remain independent.

Owned bindings carry the guard; a hub built with those bindings and its retirement
coordinator share it by default. When attaching to a pre-existing hub, construct
`OwnedIngressBindings(reconciliation=engine.reconciliation)`. An explicitly
different coordinator guard is rejected. Retirement holds exclusion through
transition/drain/reap; `claim` holds it through native preparation/publication.
The gap between these stages has no new source, retains durable transition and
consumer holds, and is not an invitation to restore the camera URL.

## Lock order

Acquire the hub guard before coordinator admission, durable policy transactions,
binding application or counted synchronous recovery. In particular, an HTTP
recovery waiting for this guard must not first register a viewer-operation lease:
otherwise retirement could wait for that lease while owning the lock it needs.
The recovery adapter now observes that order. Recorder births use their existing
recording/policy/binding barriers and do not acquire the hub guard inside them.

This is a process-local mutation barrier, not distributed ownership, external
Docker supervision, producer retirement, WebRTC-peer drain or firmware TEARDOWN.
Another server process/administrator/external client is not covered. The default
deployment remains a single app owner. Controlled first legacy migration still
requires an actual physical ingress-retirement procedure.

## Deferred convergence

Registry CRUD remains durable/best-effort when service application fails. A busy
guard or unavailable post-reload health now leaves one coalesced pending request
on the existing AddressRecovery worker. It retries on its normal30s tick without
another LAN scan. No new polling thread or unbounded job queue is created.

The event is cleared **before** an application attempt; a request arriving during
the attempt remains set for a subsequent tick. Failure restores it. Storage pause
is respected, and shutdown is checked after entering the guard and before recorder
restart, preventing a delayed request from reviving services after cancellation.
An already-sent HTTP restart is not claimed to be cancellable. Pending intent is
in-memory; the underlying registry is durable and startup reconciles it normally.

## Evidence

Tests block config write/reload, retirement and native preparation in distinct
threads and verify competing mutation cannot interleave. Native route publication
re-enters safely. Other cases reject all public mutators before I/O when busy,
prove recovery cannot deadlock a drain, preserve requests during apply, retry busy/
unhealthy state without scanning, and cancel deferred work before it restarts a hub.
Existing stream, recorder, auth and DHCP identity/credential checks remain covered.
Focused tests ran under192MiB/no-swap/50%-CPU scopes; no real hub HTTP or physical
camera commands were issued. Startup still activates no native/owned source.
