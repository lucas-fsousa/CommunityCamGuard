# Finite account lifecycle composition — 2026-10-12

`BrokerAccount` composes the verified read and heartbeat waiters under one sequence
namespace. It is a **socket-free, 120-second research owner**, not a production
persistent connection. Current PTZ caching and recording/live-view routes do not
import it. Authentication, exclusive account reservation and peer/decryption/fragment
validation belong to the eventual single socket owner.

## Limits and events

- Eight pending allowlisted reads and one reserved heartbeat slot: user reads cannot
  consume keepalive capacity. There is no submission backlog or completion history.
- One shared sequence counter, never wrapped/recycled; build failures burn numbers.
- One heartbeat due after 50 seconds; a correlated response selects the next
  35/40/fallback50-second delay. This response-relative scheduling is our conservative
  research policy, **not** an exact reproduction of the SDK's three timer phases.
- Two-second heartbeat deadline, no retransmission; timeout retires the account and
  explicitly cancels remaining reads. Transport failures require caller retirement.
- At most 512 complete input messages, 1 MiB total and 4 KiB each. Wrong-peer and
  wrong-generation traffic cannot deliver results and still consumes the work budget.
  Fragment/datagram budgets must additionally be enforced before this layer.
- Hard 120-second lifetime, unchanged by activity. Results are bounded immutable
  batches delivered immediately to the caller, not an accumulating queue.

`tick()` expires waiters and returns at most one outbound heartbeat. The caller
must call it during silence and actually transmit returned bytes once. `receive()`
processes complete decrypted frames and expires old waiters before delivery.
Neither method opens sockets or authenticates. `submit_read()` returns one prepared
request; ambiguous writes must not be replayed.

`drain()` rejects new work and stops keepalives, allowing outstanding replies for
at most two seconds. Repeated drain calls cannot extend that deadline. Empty drain
retires immediately; expiration cancels waiters. `retire()` first invalidates the
shared generation, then cancels consumers and releases the node/key reference.
Returned terminal events occur once. Socket closure remains the caller's job.
This is the boundary required before a future credential replacement, not permission
to recertify while media links or other users still own the old account.

## Evidence and next integration

Fake-clock tests cover full read capacity with heartbeat still scheduled, independent
reply ordering, cadence selection, heartbeat timeout cancelling readers, drain
completion/expiry, late/wrong-peer/generation replies, hard lifetime, input flooding,
prepare failure preserving earlier timeout results, and idempotent transport cleanup.
No real camera, worker or runtime setting was changed by this implementation.

Next compose one exclusive socket reader/writer with bounded encrypted input and
ACK servicing for this longer finite window. Then validate identity reads before
and after a correlated heartbeat in the same camera-3 account generation. Do not
extend `BrokerMaintenance`'s existing media defaults or wire this to PTZ by simply
removing time limits. Continuous operation, credential refresh, media draining and
concurrent dashboard consumers remain separate pending work.
