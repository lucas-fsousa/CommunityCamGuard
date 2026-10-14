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
