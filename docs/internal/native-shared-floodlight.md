# Shared native floodlight — 2026-11-05

The existing `white_light` boolean can now use the active native account instead
of attempting a second login. Exact-device driver evidence, operation review,
generic API permissions and standalone fallback when NO native owner exists are
unchanged. No new camera/model support or raw passthrough endpoint is introduced.

`shared_floodlight.py` reads type12 before any change, skips unchanged requests,
sends ONE typed type11 ON/OFF and requires exact type12 readback afterward.
Brightness, indicator LEDs, schedules, sirens and IR modes are not accepted by
this command. The existing SDK-mapped builder is reused without changing its
payload. Owner/enrollment changes, explicit errors or uncertain cancellation never
trigger another login, replay or inverse/cleanup command.

Lamp contracts, bounded wire exchange and strict read/write entry points live in
separate small modules. Existing status-reader imports remain compatible and
cannot admit a write spec. Reads and writes share one generation-bound inbox
and the existing broker sequence/socket/pump. Request ID, peer, session, access
ID, device ID, type and original deadline are checked; both response receipts
use the existing sole reader. A transport ACK is not application acceptance.

Some reviewed firmware omits the type11 response. Only a write actually offered
to the owner followed by timeout may be resolved with exact readback, without
resending. Such a result does NOT claim an application ACK/error0. Explicit
rejection, malformed response, unavailable/unsent command or cancellation cannot
be waived by unrelated state. Claimed cancelled writes retain their slot until
the pump retires them; cancelling an already completed unread write releases it
immediately so the lamp channel cannot remain permanently busy.

222 focused tests passed under192MiB/no swap/50% CPU (112.1MiB peak), Mypy388/
Ruff passed. Synthetic encrypted single-socket tests cover media route/B7/lamp/
heartbeat coexistence, one send, two response receipts without duplicate ACK,
unique shared sequences, read-only admission, correlation/deadline failures,
unknown outcomes, cancellation and readback. No real type11 command, illumination
change or physical acceptance was performed. Deployment/passive checks follow.
