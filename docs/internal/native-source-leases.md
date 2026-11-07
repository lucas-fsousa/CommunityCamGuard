# Per-camera sources under one native account

## Transient registry reads and refused admission — 2026-11-07

Another reproducible failure was a SQLite lock during read-only enrollment or
profile validation. Previously even the account's first enrollment read could
raise before creating any socket, leaving `cleanup_confirmed=false` and retaining
that failed account forever. A failing-before-fix regression captures this case.
The original production incident did not retain enough exception detail to claim
this was its cause.

Only numeric SQLite BUSY/LOCKED codes (including extended codes) are transient.
Message matching is forbidden; I/O, corruption, missing-schema and unknown errors
still propagate. An account that cannot revalidate retires normally and reports
busy, rather than continuing on stale credentials. If media was admitted, its
existing transport/consumer cleanup receipts remain mandatory before retry.

The driver maps temporary validation-read failure to the generic internal
`SourceValidationBusy` signal. Source recovery waits between fresh reads, observes
cancellation and does not run a child, create a replacement or authenticate while
validation is unavailable. A definitive false result still revokes admission.
This signal is handled only around validation calls; an exception from a running
child is never accepted as proof that its cleanup succeeded. Database writes and
camera commands are not replayed.

There was also a readiness-to-submit race: the account could close the inbox after
the source's readiness check but before submission. The inbox now distinguishes
that refused admission from an uncertain admitted lease. A refusal owns no channel
or decoder session; the existing source consumer still has to exit before retry.
No broker release or remote ACK is fabricated. Exceptions after a possibly accepted
submission retain the old conservative cleanup behavior.

Focused result: 91 tests passed, including a real exclusive SQLite lock released
before child admission, account startup, simulated active-media retirement,
readiness/submission race, revocation/cancellation and uncertain cleanup rejection.
Ruff/Mypy passed. Tests used192MiB/no swap/50% CPU, peak104.9MiB; Mypy was capped
separately at256MiB. No real camera I/O or production database lock was used.
The three installed sources remain on temporary RTSP; native endurance is still open.

## Shared retirement correction — 2026-11-07

Production remains on temporary RTSP after the reopened native incident. The
following defects are reproduced offline; they do not establish the original
network/decoder failure's cause or prove long-term native recovery.

The account worker previously called a broker CLOSE drain separately for every
camera. Each drain read the SAME UDP socket but offered validated replies only
to its current route. A reply for another already-closing route was consumed and
lost, so that route subsequently expired and fenced the shared account even
though the broker had replied. A regression reproduces this exact serial loss
with encrypted synthetic replies. The worker now drains all pending routes
together, offering each validated reply to every route's existing session,
sequence, peer and generation checks. No new OPEN, CLOSE retransmission, timeout
increase, login or socket is introduced. Truly absent/negative receipts still
fail the existing cleanup contract.

TCP shutdown also used to begin each two-second close only after the previous
one finished. Three quiet transports could spend six seconds there, exceeding
the source caller's five-second retirement deadline before broker cleanup.
All TCP closes now start together and receive an initial bounded pump turn
(200 ms shared across the group) before the existing final drains. Individual
deadlines, physical socket-close checks, motor STOP/siren cleanup ordering and
first-error propagation remain intact. The synthetic timing case completes the
transport phase in two seconds, not six; it is not a measured camera result.

Finally, `account_ended` is now retryable ONLY after the same confirmed camera
detach, joined consumer and successful decoder cleanup required for other
recoverable lease exits. A retained server service, not each camera, owns account
replacement. Tests prove two intents share one replacement after the old thread
exits cleanly; missing detach, live consumer or uncertain cleanup still refuse
retry. No capability, proof validity or command-replay rule changes.

Local focused regressions: 144 tests passed across shared shutdown, real
cooperative transport fixtures, simulated PTZ safety, source/account recovery,
driver startup and sanitized events. Ruff/Mypy passed. Runs were sequential,
capped at 192 MiB/no swap/50% CPU (Mypy256 MiB); test peak103.5 MiB. No real
camera I/O, speaker/light command, media migration or service restart was used
to establish these software results. The original all-camera outage is not
relabelled resolved on the basis of these tests.

## Dynamic worker ownership — 2026-11-06

`MediaLeaseInbox` and `MediaLeasePump` connect independent source callers to the
existing account reader. Slots are bounded to three including unread results.
Each lease binds a public camera ID, exact authenticated device record, account,
generation, non-reused serial and independent delivery queue. No caller reads the
broker socket, authenticates, starts a decoder or creates an account worker.

The existing reader accepts a new source while idle, retires one while a sibling
continues, and accepts an explicitly requested replacement only after confirmed
old-channel cleanup. Queued cancellation sends no OPEN. Claimed cancellation
requests cooperative TCP close; ACKs/remaining channels continue being pumped
before broker CLOSE. `CooperativeTcpMedia.begin_finish` signals this without a
two-second synchronous drain on the caller's turn.

Unknown TCP physical closure or missing broker release fences further admission.
Late cancellation/results cannot act on another serial or generation. Complete
source outcomes are retained until taken; a caller cannot free an uncertain slot
by discarding its ticket. Strict cleanup uncertainty also survives removal of a
previously retired channel, and blocks account-generation replacement.

`AccountReadLifecycle.run_media_leases` transfers one freshly authenticated
connection and one account/inventory-bound inbox to that same worker. Leases are
not an inferred feature/capability grant. Reviewed motion now propagates through
this entry point, gated on the exact device's ready lease. STOP confirmation
precedes its cooperative detach; uncertainty blocks all lease outcomes. The
production offer bridge now binds to the server account lazily, while public
source capacity remains one. See [account lifetime](native-account-lifetime.md)
for ownership, offline evidence and the deployment checkpoint.

`open_read_connection` can retain up to two explicitly requested additional
devices from the SAME authenticated inventory, not by another login. Only the
root plus requested targets are retained; unknown inventory entries are not made
admissible. Missing/offline additional targets do not invalidate an otherwise
online root; offline records cannot enter the source inbox. A matching account ID
alone is insufficient: lifecycle admission also checks exact target snapshots.

Offline regression checkpoint:246 targeted tests passed with105.4MiB peak;
earlier mypy passed409modules. Tests cover attach/detach/replacement with a healthy
sibling, idle account admission, stale tickets, queued cancellation, exact account
and inventory matching, missing CLOSE and blocked reauthentication. No physical
camera, sound, light, motor or emulator was used by these tests.

## Native consumer / per-device controls

`SharedDeviceControls` now contains the unchanged exact-device control delegates;
it does not own authentication or account shutdown. `SharedReadSession` uses that
base for the existing production path. `LeasedReadSession` uses the same delegates
but only cancels its own media ticket on stop, never the shared lifecycle.

`NativeVideoSource` can receive an exact-target shared session instead of creating
its own. The lease adapter rechecks enrollment locally, observes owner/generation
changes, and waits for confirmed channel retirement before returning. Cancellation
exceptions are re-raised only after cleanup; missing receipts have a bounded5s
wait and cannot report detached success or free the unresolved slot.

Diagnostics explicitly distinguish `session_scope=media_lease` and
`session_lease_detached`. They intentionally do NOT claim that detaching a camera
physically closed the shared UDP socket. The generic normal-run cleanup contract
already supports source detach; camera-local recovery interprets this distinction
inside the driver, without changing generic PreparedNative socket assumptions.

An offline integration test runs the actual native video source/consumer with a
fake decoder and synthetic broker: video reaches the consumer, stopping it joins
its decoder thread and closes only that channel, while the sibling keeps being
pumped on the same account. No real FFmpeg, camera or SDK login is used by that test.
Regression checkpoint:220targeted tests passed, peak106.9MiB; mypy passed411modules.
Broader consumer/control/lease regression:288passed, peak112.1MiB; no real device
commands or decoder process was used by those tests.
The preceding dynamic-worker commit `d70c86f` passed GitHub CI6078tests/1skip and Image.

## Production boundary / next integration

Production still runs one native source. These entry points are not exposed via
HTTP or a dashboard toggle. Native consumer/control bindings, server-owned account
lifetime, same-owner group publication and camera-local recovery are implemented.
Reviewed offers, PTZ authority and bounded multi-source admission remain to be connected.
The generic core already accepts a source's normal return after lease cleanup;
no generic vendor-socket retirement exception is required. See
[account lifetime](native-account-lifetime.md). PTZ remains on the accepted
production path, not the new lease entry point.
Do not increase the generic registry capacity or
declare independent SDK domains before that owner exists. Do not claim that
returning a camera lease means the shared broker socket was physically closed.

See [multi-camera checkpoint](native-multi-camera.md) for the deployed baseline.

Follow-up: a consumer can close its delivery between submission and worker claim.
The inbox now completes that queued lease as cancelled without OPEN, even if the
caller did not manage to send a separate cancellation. This retains the unread
outcome/slot until collected, rather than silently reusing it. The preceding
consumer/control commit `99ea77e` passed CI6084tests/1skip and Image.

## Deployment regression

Code `32632de` passed CI6085tests/1skip and Image and was built with512MiB/CPU50%
limits. Image `462c45d6e187` / tag `wsl-native-camera-leases` was deployed by
replacing only the app container, preserving the hub and user's recordings mount.
Build `b-210e76d55cfa`, camera3 native/active150. The old source logged confirmed
retirement at09:43:35Z; no later retry was logged in the checked interval.

One authenticated MSE connection completed60.0seconds without reconnect:
62binary messages/17864715bytes, maximum inter-message gap1.187s. These are
delivery fragments, not frame counts or absolute live latency. An initial state
query returned409; later read-only lamp queries returned200/verified, the final
one taking148ms. No write/actuation was sent. Other two cameras stayed on legacy
RTSP; base packet counters advanced2602→7760 and5128→15360, each with one consumer.
App181.3MiB/1GiB, OOM=false/restarts=0, three FFmpeg processes inside the app
(not a count of hub transcoders or unrelated WSL projects).

This deployment verifies the refactored default single-source/control path.
The new shared lease mode remains internal; **it was not used as a live test of
multiple cameras or advertised as production-ready multi-camera admission**.
