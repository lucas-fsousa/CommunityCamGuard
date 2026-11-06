# Per-camera sources under one native account

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
not an inferred feature/capability grant. Motion remains disabled at this new
entry point until per-lease PTZ authority/readiness is integrated; the current
production single-source media+PTZ path is unchanged.

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

## Production boundary / next integration

Production still runs one native source. These entry points are not exposed via
HTTP or a dashboard toggle. Next bind the native video consumer and per-device
control proxy to this lease, then integrate the driver-owned account lifetime
with generic source admission. Do not increase the generic registry capacity or
declare independent SDK domains before that owner exists. Do not claim that
returning a camera lease means the shared broker socket was physically closed.

See [multi-camera checkpoint](native-multi-camera.md) for the deployed baseline.
