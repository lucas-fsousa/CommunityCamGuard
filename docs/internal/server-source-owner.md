# Single-server ownership and startup rollback — 2026-10-24

Status: implemented and tested offline; not deployed. No production database,
camera, media container or recording was changed by this work.

## One cooperating app owner per database

Lifespan acquires `ServerOwner` before schema initialization, reconciliation or
service construction. The OS holds a nonblocking exclusive lock beside the
canonical database path: `<database-name>.source-owner.lock`. A second server
using that same local database/lock inode fails startup before creating services.
This applies even with `AUTOSTART_SERVICES=false`; API/control paths also share
state. Run one Uvicorn worker per database, not multiple workers or app replicas.

The marker's existence is not authority and it contains no PID, token, camera ID
or credentials (POSIX empty; Windows one fixed lock byte). Normal close or process
death releases the OS lock. The inode stays on disk: **never delete the lock file
to resolve contention**, because deleting/recreating it can admit a second owner
while the first still holds the old inode. Identify and stop the other app instead.

The descriptor is non-inheritable/close-on-exec. POSIX open rejects symlinks,
nonregular files, hardlinks and nonprivate/wrong-owner files; named/open inode
identity is checked around acquisition. No chmod, stale-PID kill, lockfile deletion
or unbounded retry is performed. The database directory must be trusted and the
lock inode must not be replaced externally. This is cooperative local OS locking,
not protection against a local filesystem administrator.

Linux/POSIX uses `flock`; Windows has a standard-library nonblocking byte-lock
adapter. Real kernel/process-death acceptance was performed only on Linux/amd64;
the Windows adapter has a synthetic contract test, not Windows host acceptance.
Filesystems without correct local advisory locking, different databases/hardlink
aliases, external scripts/clients and the vendor app are outside this exclusion.
No camera-wide or distributed ownership claim is made.

## Startup and shutdown lifetime

`ServiceLifetime` retains each successfully constructed service immediately. The
app retains this lifetime before initialization, so cleanup failures cannot lose
the remaining handles. Service constructors must not start untracked work before
registration; registered services start only after construction completes.

The `finally` now covers schema setup, partial construction and background startup,
not just the running ASGI application. All registered services are stopped in
reverse order off the ASGI loop. Existing best-effort media startup remains, and
native/owned source runtimes still start empty. Rollback of a failed later startup
step now cleans earlier services instead of leaving them running.

Any service stop failure retains service handles and server ownership; no new
service registration is allowed. A later cleanup attempt may retry the same
handles. Ownership is released only after every registered stop succeeds.
Unknown descriptor-close outcome is not retried against a potentially recycled
descriptor; terminate the affected app process rather than force-reusing it.

Control warmup, address recovery, storage, retention, playback warmer and recorder
stop methods now check actual thread exit after their existing finite joins.
Timed-out joins raise `SourceCleanupUnconfirmed` instead of reporting clean stop.
Recorder child retirement is still attempted before checking maintenance-thread
exit. A thread that failed to start is resource-free, not an invalid join target.
This does not forcibly kill Python threads or shorten in-flight operation limits.

## Recovery scope

This closes the duplicate-app/partial-startup gap; it does **not** restore a
persisted source selection. OS owner release does not prove firmware teardown,
broker CLOSE/B9 receipts, external shared-hub closure or all legacy FFmpeg
descendants gone. Registered service stop contracts retain their documented scope.
Verified source restoration, unknown/interrupted handoff recovery, controlled first
legacy migration and native foreground-control resumption remain open.

Tests use private temporary files, fake services and one small isolated child
that owns only a lock. They cover contention before DB setup, canonical aliases,
unsafe markers, noninheritability, process death, cleanup retry, startup rollback,
every background service's timed-out join and unchanged empty-runtime restarts.
No actual camera connection, sound/light/movement or local Docker build/restart.
Final focused owner/lifetime/background/restart group:54 passed at136.2MiB,
zero swap under192MiB/50%-CPU limits. Another59 service/recording/lifespan
regressions passed at133MiB and28 native/owned/auth/handoff regressions at141.1MiB
(groups overlap). Ruff and mypy343 source files passed.
