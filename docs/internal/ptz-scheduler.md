# Finite shared-owner PTZ scheduler

2026-10-14 — socket-free composition, not production motion activation.

`PtzScheduler` composes typed command identifiers, protected output capacity and
correlated RELEASE observations. The future sole socket owner drives it; no new
thread, socket, sleep, background timer or raw-command entrypoint is created.
Exact camera/enrollment/profile authorization remains the driver's responsibility.

## Scheduling and ownership

- Admission reserves output before constructing commands; construction failure
  releases unused output and never sends a datagram. Only one reservation can exist.
- A ready job expires after1 second without START. Pre-start cancellation or
  generation retirement emits no motion or RELEASE.
- START is returned once and charged before transport I/O, with a fixed0.1–0.5s
  gesture deadline (default0.2s). `send_failed()` after an ambiguous/partial send
  requests immediate cleanup, never another START.
- Cancellation after START shortens movement and makes RELEASE due on the next
  poll. The RELEASE window is2 seconds from its first attempt; repeated cancellation
  never extends it. At most three identical attempts are spaced at least0.5s apart.
- RELEASE remains pinned to the old session even if its allocation namespace was
  retired. The owner must not authenticate/close/replace the socket during cleanup.
- A positive correlated delivery receives a20ms observation window before success
  is finalized; a contradictory error received before finalization wins. The caller
  still polls/receives during this window, rather than immediately declaring success.
- The first correlated application response can produce at most two reserved
  receipt packets (reliable ACK when required, plus application receipt). Later
  replies still update delivery/error state but do not generate unbounded ACKs.
- Confirmed delivery or cancellation before START returns unused output capacity.
  Unconfirmed RELEASE keeps the PTZ reservation occupied; another gesture cannot
  reuse that account budget. Retire the account instead of resetting the reservation.

`PtzPacket` hides raw wire data from its repr; terminal summaries contain fixed
reason/attempt/confirmation/error indicators. A returned packet consumes its attempt
even if the caller discards it. The sole owner must send it or treat it as ambiguous;
there is no send acknowledgement API that permits replaying START.

`cancel()` is the only cross-thread method. `next_packet`, `observe` and
`send_failed` are serialized by the account owner. A caller shutting down after
START must continue polling and sending cleanup packets until terminal state; merely
discarding the scheduler is not a safe shutdown protocol. Deadlines depend on owner
polling and OS scheduling: this is not a hard real-time motor-stop guarantee.

## Evidence and next step

81 focused scheduler/budget/reservation/read-worker/preparation tests passed under
192MiB/no-swap/half-CPU limits, peak86.4MiB. Ruff and targeted mypy passed. Fake clocks
and locally generated/decrypted protocol frames cover finite movement, cancellation,
ambiguous sends, identical STOP retries, generation retirement, output floods,
contradictory replies and reserved receipt generation. No camera/provider traffic,
container restart, deployment or local full-suite run.

Next: integrate this scheduler into a separately reviewed sole-owner transport pump
with receipt routing and mandatory shutdown draining. Keep the identity-read mailbox
restricted and production PTZ unchanged until that transport composition is tested.
Physical camera3 validation remains separate, after CI and bounded deployment.

## Experimental finite transport (2026-10-14)

`ptz_transport.run_ptz_transport` now owns one supplied UDP socket for one gesture,
including close on invalid admission. It creates no socket, login or thread, and is
not connected to production. The caller must retain account exclusion and verify
the device/profile before transferring exclusive ownership.

The owner uses 20ms socket timeouts, never resends START, and drains the scheduler's
three bounded RELEASE attempts on cancellation, ambiguous writes, receive failure
or an interrupt. Original callback exceptions/interrupts are re-raised after drain
and close. Close failure is explicit on ordinary returns; exceptional exits must
be treated as uncertain ownership by the future lifecycle adapter.

Input is bounded to 64 datagrams (the 65th triggers rejection), at most 4096 bytes
each. Oversize/flood/error disables reception and cancels movement while retaining
bounded STOP sends; a 20ms wait prevents a failed socket from busy-spinning.
Only direct encrypted mode-2 frames from the pinned peer reach the scheduler.
The scheduler alone generates receipts, avoiding duplicate generic ACK handling.
Fragment reassembly, heartbeat/read multiplexing and reusable account lifecycle
integration remain pending. This finite transport is not the final shared worker.

94 focused tests passed with a 192MiB/no-swap/half-CPU limit (87.1MiB peak).
Targeted mypy passed under the same limit; Ruff passed. Encrypted synthetic STOP
success, wrong peers/plaintext rejection, failures, interruption and flood cleanup
were tested offline. No physical STOP guarantee, camera traffic or deployment.

Ownership audit: the finite owner now retires its supplied sequence namespace
before closing the socket, including invalid admission, cancellation, interrupt
and close failure. Stale callers cannot allocate commands for a closed transport.
The expanded focused suite passed 99 tests (85.9MiB peak, no swap). This retirement
is specific to the finite owner; a consumer inside a future shared worker must not
close the shared namespace when only its own gesture ends.

## Fragment transport composition (2026-10-14)

The finite transport now uses `BrokerMaintenance` for bounded reassembly (two
incomplete groups) and mode-2/session validation. It explicitly delegates inner
message ACK ownership to the scheduler; maintenance returns only fragment ACKs.
Existing maintenance callers keep their original automatic message ACK behavior.
Outer fragment group identity is not treated as authenticated session identity.

Fragment ACK attempts debit ordinary account output before send. Exhaustion or
malformed fragments disable input and request STOP without borrowing its reserved
capacity. The existing 64-datagram input ceiling also bounds fragment traffic.
Maintenance state is closed with the transport. Out-of-order encrypted reliable
STOP replies produce exactly two fragment ACKs, one reliable ACK and one application
receipt in the two-fragment test, with no duplicate message ACK path.

121 focused tests passed (87.1MiB peak, no swap); targeted mypy and Ruff passed.
No deployment or camera commands. Next: shared account lifecycle and heartbeat/read
multiplexing; this remains a finite experimental owner, not background activation.

## Shared account admission composition (2026-10-14)

`BrokerAccount.prepare_ptz` now creates the typed scheduler from the account's
existing sequence namespace: read, heartbeat and motion identifiers cannot collide.
This is a trusted sole-owner API, not a public route/mailbox or capability grant.
Driver authorization must still establish the exact device/enrollment/profile.
Only one unfinished gesture is admitted, even if a caller supplies another output
budget. Admission requires four seconds of remaining account lifetime.

Drain and retirement cancel the gesture. Drain does not report an empty account
while its scheduler is unfinished. `motion_cleanup_pending` remains explicit after
account retirement: the future socket owner must keep driving the returned scheduler
on the old socket until terminal state before closing it. It must not authenticate
a replacement account to send STOP. A completed but unconfirmed RELEASE blocks
both reads and new gestures and retires the account on its next tick/admission.
Cancellation before START allows another gesture without poisoning the account.

88 focused account/lifecycle/worker/PTZ tests passed (87MiB peak, no swap), with
Ruff and targeted mypy passing. Existing read-only worker has no PTZ admission
path; production PTZ is unchanged. Still pending: shared socket-loop integration,
thread-safe typed motion admission and receipt ownership for mixed traffic. Do not
wire this API into the old read worker: its immediate shutdown currently closes
the socket without a motion drain.

## Single-reader mixed worker (2026-10-14)

The experimental `AccountReadWorker.run(initial_motion=PtzSpec(...))` now supports
one trusted bootstrap gesture alongside identity reads and heartbeat on the same
socket. No production caller supplies this option; it is not a public PTZ mailbox.
`PtzSpec` hides device/access identifiers in repr and does not grant capabilities.
The dispatcher reserves identifiers from the same account and all output shares
one budget with protected STOP capacity.

The mixed reader polls at20ms, routes correlated RELEASE replies to the scheduler,
and generates generic reliable ACKs only for other messages. Fragment ACKs remain
ordinary output. Existing read-only callers retain their original ACK behavior.
The reusable `ptz_socket_pump` drives an existing scheduler without closing its
socket or retiring its namespace; `ptz_transport` remains the finite owning wrapper.
This separation lets the worker drain STOP in its finally block before physical
close, including on stop, drain, receive/write failure and interruption. Cleanup
send/receive counts are included on normal returns. Exceptions remain fail-closed.

`WorkerSummary.motion` exposes the finite result. Lifecycle cannot admit another
authentication after unconfirmed RELEASE even if socket close itself succeeded.
Confirmed delivery still does not prove motor state. Cleanup is bounded by the
existing scheduler deadline and may outlive the ordinary worker deadline by2s.

132 focused tests passed (88.8MiB peak/no swap); Ruff and mypy on seven affected
modules passed. Mixed encrypted PTZ/read/heartbeat, ACK counts, failure drain,
pre-start stop and lifecycle refusal after unknown STOP are tested offline.
Next: bounded thread-safe typed gesture admission while the worker is active,
then driver/lifecycle integration and CI-gated physical validation. No deployment,
new authentication, camera action or change to dashboard controls occurred.

## Opt-in concurrent gesture admission (2026-10-14)

`AccountReadWorker(..., allow_motion=True)` now exposes a separate typed
`motion_inbox`; default remains disabled. This is not a dashboard route and does
not alter the identity-read mailbox. Bootstrap and inbox modes are mutually
exclusive. Driver/enrollment authorization remains mandatory before submitting.

The inbox has one slot, including the unread result. Each ticket has generation
and monotonic serial identifiers; stale cancellations cannot target its successor.
Syntax/duration/direction are validated before occupying the slot. Unclaimed work
expires after1second. Claimed cancellation only requests cleanup: the result is
published after the sole owner completes STOP handling, never when the caller
merely releases a button. Close rejects admission and latches cancellation while
preserving the final result for take(). Unknown STOP permanently closes admission.

The owner can now execute multiple gestures on the same authenticated socket,
including before and after heartbeat, interleaved with reads. No thread/socket/login
is created by this feature. Queueing is not an unbounded backlog and consumed
results are not retained as history. Dispatcher/account still owns wire sequences.

154 focused tests passed (90.1MiB peak/no swap); Ruff and targeted mypy passed.
Tests include two-thread submit contention, cross-thread stop, stale tickets,
unread result capacity, flood cleanup and two gestures around a heartbeat on one
socket. No production caller opts in. Next: expose reviewed driver-level admission
through lifecycle and enforce enrollment/legacy-login exclusion before rollout.

## Lifecycle motion scope (2026-10-14)

`AccountReadLifecycle(motion_grants=(PtzGrant(...), ...))` enables the worker inbox
only for an immutable, bounded driver-provided scope (up to8devices, one access
identity, unique device IDs, reviewed directions). Empty/default grants preserve
read-only behavior. `submit_motion` checks device, account and direction scope
before occupying the inbox; `take_motion`/`cancel_motion` reject old generations.
Drain/stop reject new lifecycle submissions immediately. Grants hide identifiers
in repr and create no authentication, socket, capability detection or callback.

These grants must come from the driver after verifying enrollment and actual
capabilities, never from frontend JSON. They are a scope enforcement mechanism,
not evidence that a camera supports PTZ or that provider credentials are valid.
Changing enrollment requires a new reviewed lifecycle; scope cannot be mutated
while a session is running. Existing production/read-preparation callers supply
no grants and therefore cannot dispatch motion through this path.

168 focused tests passed (91.1MiB peak/no swap); targeted mypy and Ruff passed.
Coverage includes invalid/mixed-account grants, attempted scope expansion,
pre-admission refusal, lifecycle drain and stale cancellation across generations.
Still pending: the concrete driver adapter supplying these grants, coordinated
legacy-login exclusion, a production session lifetime/replacement policy and
successful CI. The60-second experiment is NOT a periodic production warmup.

### Driver adapter audit: do not promote candidates to grants

`native_ptz_policy.selected` deliberately returns all candidate directions for an
enrolled device when exact identity is not yet known. This is NOT acceptable input
for constructing a motion grant by itself. The existing `verify_ptz_target` first
uses three fresh correlated property reads: product identity, version identity and
device axis information. It intersects the model's supported directions with the
reviewed profile and actual axis evidence, then verifies the selected direction.

The future adapter must preserve that validation per authenticated generation,
exact camera/enrollment/access binding and online target inventory. Do not call
the current blocking `exchange_model_read` helper alongside the worker: it would
introduce a second reader on the same socket. Either complete preflight before
exclusive socket transfer or build equivalent evidence from the worker's typed
read outcomes. Static lifecycle grants are not a freshness mechanism. Enrollment
or credential changes require revalidation, not copying grants into a new login.

`reserve_read_handoff` already excludes legacy account users and retires the idle
cache, but the production motion adapter must retain that exclusion through final
STOP and physical close. Neither the experimental60s worker nor the120s account
model defines the eventual persistent-session renewal policy. These are remaining
integration requirements, not reasons to bypass fresh capability checks.

## Correlated evidence-to-grant adapter (2026-10-14)

`motion_evidence.grant_from_completions` now derives a scoped grant from exactly
three expected `ReadRequest`/`ReadCompletion` pairs. Requests must have distinct
sequences/paths, B7 kind and the same device/generation; every completion must
match its issued request and be a successful camera response. A missing transport
ACK is allowed when the camera response arrives first. Transport rejection,
ACK-only, booleans masquerading as numeric error codes, duplicate paths, old
sequences and cross-device/generation results are rejected.

The driver verifies exact enrollment/camera binding, normalizes product/version,
intersects model-reviewed candidates with current first-head axis evidence and
rejects an empty result. `ptz_identity_policy.identity_directions` is now shared
with the legacy preparation path, preserving its early identity rejection before
the third read. An exact previously reviewed unit must still match all identity
fields; otherwise the driver's model catalogue must recognize the variant.

`VerifiedMotionScope.for_generation` refuses another generation, an expired
ten-second collection window or a clock preceding that window. Consume it under
the same enrollment/account lock before releasing the socket; never persist or
reuse it after reauthentication. This is trusted backend evidence processing,
not proof of provenance from caller-supplied DTOs. The owner must supply its actual
issued requests and collection start time and must still verify online inventory.

94 focused evidence/legacy-target/lifecycle/worker tests passed (84.1MiB peak,
no swap); Ruff and targeted mypy passed. No camera I/O or deployment. Remaining:
wire collection/grant consumption into the driver-owned transfer on the same live
generation, then define production lifetime/replacement. Do not feed evidence from
the completed/closed60s diagnostic into a newly authenticated account.

Evidence hardening follow-up: both issued and completed tickets require their
typed structures and numeric identities. In particular, Python dataclass equality
considers generation `True` equal to `1`; that must not pass generation matching.
Malformed tickets now fail with a bounded validation error, not attribute access.
97 focused tests passed (84.2MiB peak/no swap), including these regressions.

## Collect and consume on the active generation (2026-10-14)

`MotionCollection` submits the three fixed identity/axis reads through the active
lifecycle, polls their results and submits at most one verified gesture through
the same account's motion inbox. It creates no socket/thread/login, never invokes
blocking model-read helpers and never takes ownership from the sole socket reader.
It requires an exact enrolled online target from that session's inventory. The
caller still retains enrollment and account/legacy-login exclusion for the entire
lifecycle; the collector itself does not acquire another account lock or login.

`BrokerMailbox.take_bound` consumes a result together with the ORIGINAL request
bound before send (not a request reconstructed from the reply). Lifecycle exposes
this bounded operation with generation checks. Existing take callers retain their
previous behavior and both APIs share the same single-consumption semantics.

Collection rejects partial/failed evidence, expired collection and changed account
generation. Partial admission cancels/consumes its already submitted reads. Once
verified, decoded response payloads are released. Scope consumption then supplies
an expected generation to `submit_motion`; the lifecycle checks this atomically
with admission, closing the race between successful evidence polling and a later
account replacement. Closing the collector cancels its reads, not an already
submitted gesture; use the returned motion ticket for motion cancellation.

162 focused tests passed (89.7MiB peak/no swap); Ruff and targeted mypy passed.
An encrypted synthetic end-to-end test performs all three reads, verifies current
model/axes and sends START/STOP through ONE active socket. Negative tests cover
offline/wrong inventory, absent axes, read timeout, account stop, generation change
between polling and submission, and partial mailbox admission rollback.

No production caller activates this collector. Remaining: server-owned scheduling
under the existing legacy exclusion boundary and a persistent account lifetime /
replacement policy. The research worker is still finite60s; do not deploy it as a
periodic login loop. Physical validation/deployment are separate from this evidence.

Collector cleanup follow-up: use `with MotionCollection(...) as collection` when
the caller's lifetime is lexical; exceptions close its reads without suppressing
the exception. Closing before claim returns all slots immediately, whereas claimed
cancellation remains reserved until the worker reaps it. Repeated close cannot
cancel later submissions because tickets are not reused.164 focused tests passed
(89.3MiB/no swap), including abandonment before and after dispatch. Full CI
run38580028642 for668e073 passed before this cleanup follow-up.

## Cooperative legacy contention signal (2026-10-14)

The driver-wide `own_control_channel` currently permits other control operations
to wait at most15seconds. Holding it for an entire persistent worker without a
handoff policy would starve those existing controls. `control_channel_contended`
now reports competing acquisitions so a future server owner can request bounded
drain/close before yielding. It does not release ownership, authenticate or alter
waiter ordering. Reentrant acquisitions by the current owner do not create false
demand; timeout/interruption removes the waiter count in finally.

An offline integration test drives an encrypted START, starts a competing caller,
feeds contention into the worker's cancellation predicate and confirms STOP plus
physical close before that caller enters.39 focused ownership/read/motion tests
passed (84.9MiB/no swap), Ruff/mypy passed. Production does not consume the signal
yet; the existing exclusion/uncertain-cleanup block remains intact. Unknown STOP
must still block replacement rather than yield into a new authentication.

Next: server scheduling must combine this signal with enrollment freshness,
shutdown and the existing uncertain-cleanup barrier. Do not automatically restart
a login loop after yielding. The persistent lifetime policy is still pending.

## Cancellation reaches the connection factory and lifecycle (2026-10-14)

The finite `prepare_identity_reads` adapter now combines caller cancellation with
competing control demand after acquiring the reservation. The same predicate is
passed to connection establishment and the sole reader. Contention before auth
avoids login; contention during auth uses the factory's existing bounded cleanup;
contention during reads ends the diagnostic without waiting for its60s deadline.
The reservation still covers physical close. A close failure poisons the shared
channel, so yielding cannot silently permit an unsafe replacement authentication.

`AccountReadLifecycle.run` also accepts a cancellation predicate, matching its
batch API. A trusted connection factory must observe that predicate itself while
authenticating; after transfer the worker observes it. Offline tests submit an
authorized gesture, cancel after START and verify STOP on the same socket before
close. Missing STOP confirmation leaves the lifecycle blocked. No automatic retry
or thread is introduced, and no production scheduler activates this lifecycle.

72 focused tests passed with87MiB peak and zero swap. This is cooperative bounded
cancellation, not a guarantee of FIFO fairness or a measured15s live latency SLA.
Persistent lifetime, server-owned scheduling and physical validation remain open.

## Background admission versus foreground demand (2026-10-20)

Production PTZ warmup now uses `own_control_channel(wait=False)` before credential
renewal/cache preparation. Unlike a foreground action, it neither waits behind an
existing owner nor increments the contention count. Registered foreground waiters
take precedence even if the underlying lock has just become free. Nested renewal
remains reentrant under the same ownership; uncertain cleanup still blocks entry.
A skipped warmup returns false and releases its preparation event normally.

Previously background preparation used the same waiting path as user controls:
a shared worker consuming the contention signal could consequently drain at every
maintenance tick. This change removes that specific interference in the actual
production warmup caller. It does not shorten existing authentication once started,
promise FIFO fairness, extend token lifetime or activate the research worker.
52 focused tests passed with121.4MiB peak/no swap, including a real competing thread
that cannot authenticate or make the current owner yield. No camera traffic.

Deployed6110502 after full CI39502934954 passed (54s), image a0f7fbece957,
app started2026-10-20T21:46:52Z. Bounded512MiB/no-extra-swap/half-CPU build;
only app recreated and go2rtc start preserved. Installed hashes match. Read-only
acceptance:3online/3recording and nonempty MP4 files2s old (48.1MiB/no swap);
camera3 catalogue retains audio modes and seven static controls (42.2MiB/no swap).
No physical action or native media activation. This also deploys pending472946a.

Concurrency follow-up: a real competing thread now exercises the complete finite
adapter, not a mocked contention predicate. It can enter only after socket close
and cache unquiesce; an injected close failure instead makes it receive
ControlChannelBusy with the shared cleanup barrier set.74 focused tests passed
(86MiB/no swap), including both outcomes. No camera or provider traffic.
