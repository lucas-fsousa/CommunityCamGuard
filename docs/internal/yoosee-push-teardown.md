# Push/relay hangup — offline SDK 6.45 evidence, 2026-10-01

Binary hash and A4/E4 context: [platform provenance](yoosee-platform-sdk-versions.md).
This is a socket-free wire encoder, **not** an enabled relay transport or proof of
remote teardown. It has no production callers, API, credential loading or send loop.

## Native call chain

`iv_push_start_hanghup` (`0x27adcc`, 64 bytes) passes the selected channel at
session `+0x188`, token descriptor at `+0x28`, and reason zero to
`iv_send_hangup_frame` (`0x27ab04`, 712 bytes). The wrapper returns -1 regardless
of the send result; do not interpret that constant as a wire error or ACK.

The sender allocates `26 + token_length`, zeroes it, populates the following
fields, computes the checksum, and calls `ivudp_sendto` at `0x27ad4c` with the
selected node's address. This is **not** the existing broker B9 hangup packet.

| Offset | Width | Source/meaning |
| --- | --- | --- |
| 0 | 1 | 3 |
| 1 | 1 | `0x0b` |
| 2–3 | 2 | zero in the recovered branch |
| 4 | 2 LE | Body length: `6 + token_length` |
| 6 | 2 LE | Checksum below |
| 8 | 4 LE | Selected channel's parent session `+8` (codec `push_id`) |
| 12 | 8 LE | Parent terminal `+0x2e0` (codec `access_id`) |
| 20 | 1 | Reason argument: zero from this wrapper |
| 21 | 1 | Push session `+0x22` (codec `session_type`) |
| 22 | 2 LE | Token length |
| 24 | 2 LE | Push session `+0xdc` (codec `session_id`) |
| 26 | variable | Token descriptor data |

`iv_push_session_app_new` (`0x277e34`, 1056 bytes) establishes their provenance:

| E4 field | Push-session destination | Meaning |
| --- | --- | --- |
| `+0x2c`, u32 | `+8` | Push ID, distinct from the MTP link at E4 `+0x28` |
| `+0x1d`, byte | `+0x22` | Session type |
| `+0x1e`, u16 and data `+0x88` | Token descriptor `+0x28` | Copied token |

The short session ID at `+0xdc` is generated locally: three `rand()` calls at
`0x277fcc`, `0x277fd4`, `0x277fec`, combined and truncated to u16 at `0x278000`.
It is **not** copied from the MTP link or E4 push ID; this is not evidence of
cryptographically secure randomness. The access identity belongs to the terminal,
not to E4. Encryption fields are separate and are not exposed by our parser.

`p2p/push_context.py` now extracts only these proven E4 fields, reusing the bounded
envelope/relay-table validation and requiring both the expected device and MTP link.
Its representation hides the token and push ID. ID matching is correlation, not
authentication: a future caller must supply an authenticated/decrypted frame.
No production integration, relay address selection or automatic hangup is added.

Helper `0x27a8c0` (148 bytes) reads four little-endian 16-bit words beginning at
offset 20, rotates word i left by i bits (0 through 3), and XORs them. The sender
XORs this result with body length and writes offset 6 (`0x27ad0c–0x27ad28`).
Only the first two token bytes participate; this is not cryptographic integrity.

Our isolated `p2p/push_teardown.py` reproduces the reason-zero variant, validates
integer widths without silent truncation, and rejects tokens shorter than two
bytes (the native checksum would read beyond the allocated body) or longer than
4096 bytes. The maximum is a local safety policy, not a vendor protocol discovery.
Tests use synthetic values, a manually calculated checksum and boundary failures.

## Local cleanup is separate

`iv_push_session_free` (`0x278254`, 576 bytes) releases cipher state, relay nodes,
channels, token storage, KCP objects, users and ring buffers. That function does
not itself call the hangup sender. Local memory/socket cleanup and remote hangup
must not be conflated. The current B9 release receipt does not prove this push
session is closed.

The recovered direct caller of both hangup and free is `iv_push_link_reset_proc`
(`0x2560dc`, 1264 bytes). It frees timer `+0x308` first, then calls hangup only
when channel byte `+0x31c == 1` (`0x256180–0x25619c`). It subsequently frees timers
`+0x2f8`, `+0x2d8`, `+0x2e0`, `+0x2e8`, `+0x2f0`, `+0x298`, frees the push session
at `+0x108` (`0x256498`), clears its pointer and resets the ready flag. The local
path does not wait for a hangup ACK or retry the send before freeing. This is not
proof that the remote relay has released resources, nor a global ACK inventory.

### When ready is set

`iv_timer_check_push_live_connect` (`0x255878`, 508 bytes) calls
`iv_check_push_session_connected` at `0x255958`. A positive result updates channel
status to 4, calls `iv_push_notify_connect_ready` if user-accept was zero, sets
user-accept to 1 and writes ready byte `+0x31c = 1` at `0x2559d8`.
If not connected and elapsed time exceeds 5000 ms, it calls `iv_push_link_reset`
at `0x255a48`. Do not treat receipt of E4 alone as ready.

`iv_check_push_session_connected` (`0x27c544`, 148 bytes) iterates channels and
returns true if any signed 16-bit counter at channel `+0x1c` exceeds 5. The timer
also sets the selected channel's counter to 75 when its state byte `+0x20 == 2`
(`0x2558d0–0x255910`, SDK log calls this certified). These are SDK predicates,
not yet a reproduced authentication handshake. Receive-side certification and
recertification transitions are mapped in [certification evidence](yoosee-push-certification.md).
Counter increments and upstream validation still need tracing.

## Validation limits / next checkpoint

Disassembly remained sequential under 256 MiB/no-swap/50%-CPU/40-second caps;
observed peaks were at most 40.4 MiB. No camera, broker or relay was contacted.
The codec does not make an E4 solicitation safe yet. Field population, outer timer
cleanup and the ready predicate are now mapped offline. Next: trace remaining
counter transitions and pre-ready remote lifetime, then define
an explicitly bounded diagnostic. Do not enable relay advertisement in the default
live path. Synthetic tests also cover wrong device/link, malformed envelopes,
every truncated prefix, relay bounds and credential-free representations.
