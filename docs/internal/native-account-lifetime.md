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
continue. The integration also uses the generic SourceRegistry and camera-local
recovery: one injected decoder failure is repaired with a new lease after normal
backoff while siblings continue, then a source is explicitly stopped/replaced.
Five OPEN/CLOSE pairs still use one login, one broker socket and one SDK-owner
scope. The detached camera's registry slot becomes idle while the account remains
active. Every decoder/consumer retires before service shutdown releases the account
and server owner. No real SDK login, FFmpeg child, camera, sound/light/motor action
or emulator is used by this integration test.

Tests also cover SDK contention without login, changed credentials without
reauthentication, confirmed opening failures, unknown physical close, scope-exit
failure, server-lock retention, lazy startup and reverse source/account shutdown.

`native_lease_recovery` classifies only confirmed lease detach and joined consumer/
decoder retirement; it does not require closing or reacquiring the shared broker.
Known local decoder/transport failures may retry with normal backoff. Unknown
protocol/authentication/cleanup failures cannot. Fixed retry history distinguishes
lease detach from shared-socket closure without persisting payloads or credentials.

Safety follow-up: an unexpected opening exception (including a TCP-close failure)
must not publish a clean lease outcome merely because broker CLOSE was confirmed.
The worker now marks outstanding leases uncertain on unhandled exits. Regression
tests require blocked outcomes/no slot reuse even with a positive broker receipt.
Unsafe siren cleanup also blocks lease outcomes; this is tested with a synthetic
unsafe actor only, without emitting sound or sending a command to any camera.
The preceding owner/service commit `5a4b03f` passed CI6098tests/1skip and Image.

Latest targeted integration/cleanup regression:202passed, peak125.3MiB, CPU50%
and MemoryMax192MiB. Full mypy passed415source modules under a256MiB ceiling.

## Reviewed offer integration

Production offers now bind lazily through `AccountVideoSource` and the driver's
server-owned `NativeAccounts` service. Construction, subscription and startup
never authenticate; the admitted source run prepares the exact singleton cohort,
waits for readiness, then constructs the consumer with a `LeasedReadSession`.
The existing public admission domain/capacity remain unchanged (one native source),
preserving restart checkpoints. Other cameras are not automatically migrated.

An explicit source intent spans the complete recovery loop, including backoff.
Lease replacement therefore reuses a healthy account; ending the last source
stops/joins the account and requires confirmed cleanup before releasing its SDK
reservation. This also permits switching back to legacy controls. Unknown cleanup
retains handles and closes admission. Sources cannot adopt an unmanaged account.

Reviewed per-device PTZ grants propagate from the offer to the account and exact
binding; ordinary PTZ evidence alone is still insufficient. Motion requires that
device's active, non-cancelled, ready media lease. Detach cancels motion and waits
for a STOP receipt before TCP/broker close. Missing STOP confirmation fences all
lease outcomes. Synthetic encrypted tests cover that order, a healthy sibling,
unleased-device rejection and repeated identical STOP on missing receipt.

Final targeted source/service/worker regression passed137 tests (151.5MiB peak)
under192MiB/CPU50% limits. Mypy passed416modules; Ruff passed. Full GitHub CI passed
6133tests/1skip and all frontend contracts; Image passed too. These are offline
results, not a new physical motor test. Bounded public multi-source admission remains.
The core's existing normal-run cleanup
contract already supports a detached source lease; do not invent a requirement
to physically close the shared broker socket for each camera.

See [source leases](native-source-leases.md) and [driver architecture](0001-pluggable-camera-drivers.md).

## Deployed regression checkpoint

Code `5ceece1`, image `d1835e054877` / `wsl-shared-native-offer`, build
`b-ba01cbf86a08` replaced only the app using a512MiB/CPU50% image build. Camera3
resumed native/active156 through the account-bound offer. Public capacity/domain
remain unchanged, the other two cameras remain legacy RTSP, and the hub/tunnel
were not replaced. CI6133tests/1skip and Image passed before deployment.

A single authenticated MSE connection completed60.0s:62binary fragments/
51510162bytes, max interarrival1.537s, no reconnect. A lamp GET initially returned
409, then200/verified on the existing shared session, with no write. This does not
establish the initial409's cause. The original archive started a new epoch at
14:36:45Z; its MP4 grew to8388608bytes at the check (still open, not a closed-file
acceptance). No sound/light/motor command was sent. No newer retry event than
the prior12:40:31Z event was present during this observation. Fragment delivery
is not a decoded-frame or absolute-latency measurement.
App217MiB/1GiB, OOM=false/restarts=0, three FFmpeg processes inside the app (not
counting hub transcoders/unrelated projects). Both legacy sources remained active,
with one base consumer each. The first packet-counter snapshot after replacement
was garage9684/yard8196; do not compare these reset counters to the old container.

### Previous checkpoint (classic offer)

Code `c82a053` passed CI6119tests/1skip and Image. The image build used512MiB/CPU50%
limits; image0250f10f1842/tag `wsl-native-account-owner` replaced only the app,
preserving the hub and user's recordings-host mount. Build `b-a5d5dd07a803`,
camera3 native/active153. The service is registered but no shared-account request
is made by current production offers; this does NOT activate a new login or
silently migrate another camera.

A single authenticated MSE connection completed60.0seconds:62binary fragments/
31604455bytes, maximum inter-message gap1.877s, no reconnect or new retry event.
Fragments are not decoded frames, and interarrival gap is not end-to-end latency.
The old source's confirmed retirement event was at12:40:31Z; no later retry was
logged in the checked interval. Read-only lamp state initially returned409, then
returned200/verified267ms without a configuration write. Yard/garage ingress
packet counters advanced6509→12471 and8372→15831; their base consumers changed
from1to2 during the observation. No cause is inferred from that consumer change.

App194.4MiB/1GiB, OOM=false/restarts=0, three FFmpeg processes inside the app
(excluding hub transcoders/unrelated WSL projects). The closed native archive
`20261106_124048_e06d20cb905a342ba897934a133e857e9.mp4` was inspected without
playback:299.885667s, HEVC1920×1080/AAC16000Hz mono. The next124547segment used
the same epoch. This is a short regression of the unchanged production path,
not live acceptance of three native cameras or a new physical-control test.
