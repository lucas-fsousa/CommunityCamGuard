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
