# Continuous account lifetime — offline implementation

2026-10-20. Explicit `AccountPolicy.CONTINUOUS` now passes through the generation
lifecycle, sole reader, dispatcher, account and encrypted input/output accounting.
The default remains `FINITE`; no production caller or diagnostic endpoint selects
continuous mode. No new setting, camera capability, thread or login loop is added.

## What changed

The finite worker previously closed after60seconds and its account after120seconds.
Both input layers and output also had cumulative512-packet/1MiB allowances. Merely
removing a timer would still terminate a healthy connection as traffic accumulated.

Continuous mode keeps the same generation and socket until explicit shutdown,
drain, cancellation, heartbeat loss, sequence exhaustion or transport/protocol/budget
failure. Heartbeats remain correlated on the existing namespace and retain their
bounded reply deadline; success is not an assumption of indefinite credential TTL.
No failed operation is replayed and no replacement login happens automatically.

Input (wire and decoded frames separately) and output use constant-space dual token
buckets: initial capacity512 packets/1MiB, replenished at that capacity per60seconds.
This is a burst plus sustained-rate limit, **not** a claim of a strict rolling-window
512-packet maximum. Long idle time cannot accumulate more than one bucket. Excess
traffic fails closed without a growing queue or sleeping inside admission.
Zero-byte input still costs one packet; oversized datagrams remain rejected.

Eight read slots, two fragment groups,4KiB wire ceiling, deadlines, generation
checks and no sequence recycling remain unchanged. PTZ's six-slot START/STOP/receipt
reservation remains unavailable to ordinary writes across replenishment; refilling
does not mint additional gesture slots. Failed/attempted sends are not refunded.
Cancellation still drains STOP on the old socket. Unknown STOP or physical close
blocks generation replacement. Invalid/non-monotonic budget clocks fail closed.

## Evidence and limits

Synthetic encrypted-peer tests run30minutes of virtual time with more than1500
successful reads, over3000 writes and at least35 correlated heartbeats in one
generation, then cancellation/one physical socket close. No real-time30-minute
camera soak is claimed. Separate tests cover finite-mode non-refill, packet/byte
exhaustion, idle credit caps, invalid policy/clock, input flood, heartbeat loss,
and confirmed/unconfirmed STOP during continuous-mode cancellation.

Combined focused regression:223 tests passed,7.806s,95.4MiB peak/zero swap under
192MiB/no-swap/half-CPU limits. Ruff and targeted mypy passed (79MiB/zero swap).
No browser/emulator, build, container restart or camera command was involved.

This is a lifetime implementation, not continuous native video delivery. Remaining:
server-owned scheduling bound to enrollment freshness/shutdown/foreground contention,
then attaching media routes to this same authenticated reader without a second
login, then single-producer delivery/fallback and camera3 acceptance. Do not call
it indefinitely under the legacy global lock without the cooperative handoff policy.
Audio acceptance and factory reset remain deferred while the owner is away.

## On-demand server ownership adapter

`SharedReadSession` now runs the continuous lifecycle inside the existing PTZ-cache
handoff and driver-wide account exclusion boundary. It creates no thread; its
trusted caller owns the thread and must supply server shutdown/cancellation. Each
instance permits exactly one run. Busy acquisition returns immediately without
queuing, signaling foreground contention or automatically authenticating later.
No production service or public API instantiates it yet.

The adapter fixes a single camera/device target and exposes only identity-read
submission/results. It does not admit motion, light, sound or arbitrary commands.
It checks the registered enrollment before opening, then at most once per second,
comparing credentials as well as timestamps/association. Deletion, replacement,
database failure, explicit stop or shutdown causes retirement. Foreground control
demand is checked on every pump iteration and during bounded authentication.
Cache retirement, worker lifetime and confirmed physical close remain under the
same reservation. Confirmed factory failure yields safely; uncertain close poisons
the existing shared-channel barrier, blocking another login.

The active-session database read joins camera and enrollment atomically, performs
no schema initialization, sets SQLite query-only mode/50ms busy timeout and closes
its connection explicitly. The polling interval is not a hard real-time revocation
SLA: OS scheduling/file I/O may still delay it. The adapter deliberately does not
hold the per-camera operation lock indefinitely, which would prevent foreground
requests from reaching the account's contention signal.

77 focused tests passed,107.1MiB/no swap; Ruff/mypy passed. Synthetic encrypted
transport covers reads after the former deadline, shutdown, credential/association
changes, database failures, bounded busy admission, factory cancellation and a
real competing thread admitted only after physical close/cache unquiesce. Temporary
SQLite tests verify registered-camera matching, query-only reads, lock failure and
connection closure. No live camera traffic, deployment or additional server worker.

Next: attach the media-route lifecycle to this owner and connect it to server
source scheduling. The adapter is not yet a live native video producer; existing
RTSP/dashboard paths remain unchanged. Do not schedule periodic restarts of it.

Follow-up: [broker media-route integration](native-shared-media-route.md) now adds
typed one-route OPEN/CLOSE on that same reader, with independent cleanup capacity.
This does not yet attach the TCP video channel or enable simultaneous motion/media.
