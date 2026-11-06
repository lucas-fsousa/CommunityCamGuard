# Server-owned native account lifetime

## Implementation — 2026-11-06

`NativeAccount` owns one certified login/UDP reader and up to three explicitly
enrolled cameras. Only the root token is used for authentication; other targets
come from that SAME authenticated inventory. Equal account IDs alone do not
authorize a camera binding. Each per-camera binding remains an exact enrollment,
target and generation, not a brand/model capability grant.

`NativeAccounts` retains the account and its single worker thread. It requires an
acquired `ServerOwner`; construction/server startup performs no camera I/O.
Explicit driver admission starts a worker once. Concurrent matching requests
reuse it; conflicting scopes or uncertain retirement cannot create another login.
A cleanly retired worker may be replaced only by another explicit request.
Unknown failures retain the account/thread handles and SDK fencing; shutdown
cannot release the server lock while the account's cleanup is unconfirmed.

The account retains `reserve_read_handoff` throughout authentication, pumping and
physical retirement. Root credential revalidation is bounded to once per second,
not a SQLite query on every TCP pump iteration. A change ends the account; it does
not silently renew credentials. Individual bindings revalidate their own enrollment.
Cleanup is confirmed only AFTER both broker and SDK-ownership contexts exit.

## Generic server hook

`CameraDriver.runtime_services(owner)` is optional and defaults to an empty tuple.
It returns lazy, stoppable resources; the generic server does not know vendor
protocols, account IDs or feature catalogues. Yoosee registers `NativeAccounts`.
Resources are retained by `ServiceLifetime` BEFORE native source runtimes, giving
reverse shutdown order: source consumers → driver accounts → exclusive server owner.
Partial startup and failed shutdown retain the existing fail-closed semantics.

## Group control publication

`reserve_native_group` accepts only a bounded explicit public-camera-ID tuple.
Its members share one private owner identity. Multiple per-device control proxies
can be published only under that SAME owner. Duplicate camera publications,
unlisted devices and unrelated owners remain rejected. Legacy single-owner
behavior is unchanged. No wildcard publication or unconditional registry-capacity
increase was added. Unknown group exit stays fenced.

## Offline integration evidence

The real account/lifecycle/worker, three native consumers and control-publication
registry run against synthetic encrypted broker replies and fake decoders. Three
consumers are active together; one is stopped and replaced while the other two
continue. Four OPEN/CLOSE pairs use one login, one broker socket and one SDK-owner
scope. Every decoder/consumer retires before service shutdown releases the account
and server owner. No real SDK login, FFmpeg child, camera, sound/light/motor action
or emulator is used by this integration test.

Tests also cover SDK contention without login, changed credentials without
reauthentication, confirmed opening failures, unknown physical close, scope-exit
failure, server-lock retention, lazy startup and reverse source/account shutdown.

## Remaining public integration

The service is registered but idle unless explicitly requested by a driver.
Current production offers still use the previously accepted single-source path;
no other camera is silently migrated. Remaining delivery work: bind reviewed
source offers/recovery and per-lease PTZ authority to this account service, then
enable bounded multi-source admission. The core's existing normal-run cleanup
contract already supports a detached source lease; do not invent a requirement
to physically close the shared broker socket for each camera.

See [source leases](native-source-leases.md) and [driver architecture](0001-pluggable-camera-drivers.md).
