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

The names identify push-session fields only. Their population from E4/certification
still needs tracing; they must not be substituted with similarly named MTP IDs.

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
must not be conflated. Timer ownership and the outer channel close orchestration
remain to be mapped, as do acknowledgement/retry behavior and pre-certification
cleanup. The current B9 release receipt does not prove this push session is closed.

## Validation limits / next checkpoint

Disassembly remained sequential under 256 MiB/no-swap/50%-CPU/40-second caps;
observed peaks were at most 40.4 MiB. No camera, broker or relay was contacted.
The codec does not make an E4 solicitation safe yet. Next: trace session-field
population, hangup callers and outer timer teardown, then define an explicitly
bounded diagnostic. Do not enable relay advertisement in the default live path.
