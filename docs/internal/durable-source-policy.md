# Durable source intent and read/apply barrier — 2026-10-23

Status: internal persistence and legacy-admission guard implemented/tested. There
is no new source-selection API, dashboard switch or automatic camera migration.
No production policy was changed and no camera was contacted by these tests.

## Stored contract

`db/source_policy.py` stores one non-secret row per opaque public camera ID in
`media_source_policy`. It contains `mode`, `phase` and a monotonic revision, with
no address, driver-native identifier, URL, password, access token or worker port.

Modes are `legacy_rtsp`, `owned_rtsp` and `native`; phases are `active`,
`transition` and `blocked`. Missing rows preserve existing active legacy RTSP.
Reading a pre-migration database does not create the table. Application startup
initializes its schema without selecting another mode for existing cameras.

`begin_change` uses an immediate SQLite transaction and revision comparison; only
a configured camera in an active phase can begin a transition. `activate` requires
the matching transition revision, and `block` latches an active/transitioning
selection after uncertain cleanup. Blocked/corrupt rows cannot be optimistically
reset or activated. Operator recovery is not exposed yet. Invalid persisted
fields fail closed for that ID; storage read failures never default to direct RTSP.

This is intent, not an ownership receipt or driver capability grant. The future
coordinator must verify each device's supported source factory through its driver,
own admission and physically retire the previous source before preparing and
activating the next one. Calling a database method does not perform those actions.

## What the guard now prevents

The go2rtc renderer includes direct camera URLs only for active `legacy_rtsp`
selections. Transitioning, blocked, native and owned selections omit the base and
both quality variants. An active `owned_rtsp` selection is deliberately unavailable
until runtime loopback bindings are integrated; it never falls back to the camera
URL merely because a worker binding is missing.

`Go2rtc.write_config`/`write_config_if_changed` retain a policy read transaction
through rendering and the actual private-file write. A concurrent transition
writer therefore cannot return committed while an earlier policy snapshot is
still writing the old camera URL. A standalone `build_config` result is just a
snapshot; publishing it independently must preserve the same read/apply barrier.

The recorder checks the policy during every new legacy admission, retaining the
read transaction until the child is tracked. It still honours the in-memory
`Recorder.ingress` gate and global storage pause. A fresh Recorder or an address/
registry resync cannot admit the old consumer just because an in-memory hold was
lost. Existing recording children are not terminated by merely writing intent;
the coordinator must still suspend/drain them explicitly.

## Scope remaining

`media/browser_ingress.py` now binds legacy browser admission to the configured
opaque ID and exact durable selection revision. The primary proxy accepts only
base/HD/web aliases and a single `src` parameter; the temporary bridge retains its
HD/web-only MSE protocol. Neither accepts arbitrary producer URLs. Nonlegacy,
transitioning/blocked or corrupt intent rejects new legacy viewers. The existing
session watcher checks both authority and this lease: a revision change, removed
camera or database failure closes signaling/MSE and awaits its upstream/task
cleanup. Returning to legacy under a later revision does not revive old leases.
Legacy recovery holds a policy snapshot through its hub request and returns409
instead of invoking recovery for nonlegacy intent.

This polling guard is not atomic with upstream connection establishment: a viewer
admitted just before a committed transition can still reach the hub before its
next check. The future coordinator must gate admission and await drains before
retirement/replacement. A WebRTC peer may survive closure of its signaling socket.
These guards do not modify a running go2rtc source, retire independent WebRTC
peers, prevent all external clients from bypassing the dashboard, serialize all
runtime reload callbacks, or prove physical old-source closure. Runtime bindings,
the actual coordinator and controlled migration of the old shared ingress remain
pending. Native audio/recording and hardware acceptance are also separate. These
browser changes are not deployed; startup still activates no native/owned source.

## Evidence

Tests preserve default configuration, exclude only the selected camera after
transition/activation, preserve intent after DHCP/registry updates and a new
renderer/Recorder instance, enforce stale-revision/blocked-state rejection, and
fail closed on corrupt policy/storage failure. Concurrent tests hold an old file
write or recorder birth and verify the policy change cannot commit until that
application completes; afterward the old URL/consumer cannot be recreated.

The expanded82-test renderer/recorder/lifespan run passed at143MiB with zero swap
under a192MiB/50%-CPU scope. All process creation in these policy cases is mocked.
See the separately tested [owned ingress](owned-rtsp-ingress.md) for real local
socket retirement and [recording gate](recording-ingress-retirement.md) for the
in-memory consumer hold.

Browser guard evidence:54 focused alias/lease/primary-proxy/session cases passed
at144.2MiB/no swap;117 temporary/native/recovery/auth-inventory/origin/redaction
regressions passed at150.1MiB/no swap. Mypy325 modules and Ruff passed. Upstreams
were synthetic in-memory sockets; no camera, media process or deployment occurred.
