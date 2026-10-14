# Typed PTZ command reservation for a shared owner

2026-10-13. Offline implementation only; no worker wiring, camera I/O or deployment.

`BrokerSequences.reserve(count)` atomically reserves1–64 consecutive identifiers
within the single account owner's sequence namespace. Exhaustion rejects the whole
group without consuming any identifier. It never wraps or recycles. Atomic here
means all-or-nothing for the existing single-owner API, not thread-safe allocation.

`ptz_command_reservation.reserve_ptz_commands` validates the supplied account
generation, identifiers and reviewed axis set before reserving three identifiers:
START, RELEASE and one application receipt. It builds both encrypted commands
before exposing START. Encoding failure burns the reservation, but sends nothing.
This avoids successfully allocating START at the end of the namespace with no
remaining identifier for RELEASE. Interleaved reads/heartbeat allocate after the
whole reserved group.

The caller still proves exact enrollment/model/device support through the driver.
Supplying a reviewed direction set is not capability detection or authorization.
No generic raw-command interface, socket, reader, timer or automatic motion exists
in this module.

## Attempt semantics

- `take_start` consumes the single START attempt **before** the caller's transport
  send. Ambiguous send failure never permits another START.
- Cancellation or generation retirement before START rejects motion entirely.
- After a START attempt, RELEASE remains available for cleanup on the pinned old
  socket even if allocation has been retired. It must never be sent through a
  newly authenticated/replacement generation.
- At most three RELEASE attempts return the **same prebuilt datagram**. No fresh
  sequence/message/request identifier and no new motion command is generated.
- Cancelling after START does not recall the datagram or cancel mandatory cleanup.

This reserves identifiers and builds commands, **not output capacity or time**.
The sole worker must still reserve STOP send/receipt capacity, honor the existing
finite gesture and release deadlines, validate correlated responses with error
precedence, and prevent physical socket retirement before cleanup. One reserved
application-receipt identifier does not grant unlimited response acknowledgments.
The current `NativePtzRoute` and deployed PTZ behavior are unchanged.

## Validation and next step

56 focused reservation/sequence/worker/preparation tests passed under192MiB/no-swap
and half-CPU limits, peak85.1MiB. Ruff and targeted mypy passed. Golden payload checks
decrypt the generated datagrams locally. Tests cover exact sequence exhaustion,
insufficient capacity with no partial allocation, interleaving, unsupported profile,
wrong generation, cancellation, ambiguous START and identical bounded STOP retries.

Next: reserve wire/queue budget and implement correlated PTZ results in the sole
worker, separately from enabling motion. Keep the read-only mailbox allowlist and
existing production motion/STOP lifecycle intact until the complete adapter is
validated. This is not physical STOP confirmation or production shared-session PTZ.

2026-10-14 follow-up: [output reservation and RELEASE observations](ptz-shared-output.md)
now provide bounded send accounting and a socket-free correlation tracker. The
read worker uses the accounting but still admits no motion; scheduling and cleanup
composition remain pending.
