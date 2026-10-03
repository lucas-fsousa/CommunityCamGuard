# Yoosee SD playback strategy — bounded offline evidence

Inspected 2026-10-03: Google Play 6.45 ARM64 split APK,
`lib/arm64-v8a/libiotvideomulti.so`, SHA-256
`510bc51a5545907b53b3f1e61625a7e453d4e72d69f658a1dca75838ec0756aa`.
This is command **25**, not playback speed command 24 or a recording-policy
setting. No camera command, runtime capability or dashboard control was added.

## Proven request and response behavior

| SDK location | Evidence |
| --- | --- |
| `PlaybackPlayer::set_playback_strategy`, `0x154280` | Receives the strategy in `w1`; stores it into player `+0x218` at `0x1542ac`, before dispatch or response. |
| `0x154468–0x154494` | Selects command `0x19`, allocates four payload bytes and stores the original 32-bit value. On this ARM64 build the payload is little-endian. No platform branch is present in this setter. |
| `0x1544bc–0x1544cc` | Dispatches through the connection's virtual command sender. This is not a standalone UDP frame. |
| `PlaybackPlayer::playback_strategy`, `0x154608` | Returns player `+0x218`; it reads local requested state, not camera-confirmed state. |
| Reply lambda `0x15b590`, check at `0x15b5cc` | Passes the response body to `isAckOK`. Success/failure branches log their respective result; the inspected lambda does not replace the cached strategy with a camera-reported value. |
| `isAckOK`, `0x132f30–0x132f5c` | Empty response is false. For a nonempty response, only first byte `1` or `2` returns true; trailing bytes are not inspected here. |
| Lambda return `0x15b750` | Returns `1` after either branch. Do not interpret that callback-dispatch return as camera success. |

The reply rule differs from the permissive video-definition callback described in
[native-video-definition.md](native-video-definition.md). A generic “empty ACK
means success” rule would therefore be wrong. These checks occur **after** SDK
message dispatch; they do not replace route, command or pending-request correlation.
Even a correlated application ACK is not proof of a physical playback effect.

## Indirect caller and startup default (follow-up)

The direct-call scan missed a virtual call, now resolved from ELF relocations:

* `_ZTVN8iotvideo14PlaybackPlayerE` starts at `0x29fea8`. Relocations at
  `0x29fed0` and `0x29fed8` reference the strategy setter/getter respectively.
  Constructor `0x1537b8–0x1537c0` installs the primary vptr at table `+0x18`,
  making the setter's virtual slot **+0x10** from that address point.
* `PlaybackPlayer::play`, `0x155ec8`, loads the cached strategy from `+0x218`.
  `0x155ecc` skips the call if zero. Otherwise `0x155ed0–0x155edc` invokes
  that exact virtual slot, forwarding the cached value as `w1` and player as
  `x0`. This follows `set_opt_conn_params` and precedes `BasePlayer::play`.
* The constructor initializes `q0` to zero at `0x15379c`. At `0x1537e8` it
  sets `x8 = this + 0x1f0`; `stur q0, [x8, #0x1d]` at `0x1537f4` clears
  bytes `+0x20d..+0x21c`, including the complete four-byte strategy cache.
  Thus a freshly constructed player has zero here without calling the setter.

In the inspected `play` branch, connection state must compare greater than 4 and
the connection-parameter buffer at `+0x50/+0x58` must be nonempty to reach this
replay. The other state branch calls `BasePlayer::play` directly. Numeric state
names are not established by this trace; it does not prove a complete network
handshake or that every SDK entrypoint behaves identically.

Consequences: the default startup path can omit command 25. Do not make a guessed
strategy request a prerequisite for SD listing/playback, and do not send a zero
payload just because the constructor uses zero locally. A nonzero requested value
can be replayed by `play` even if its earlier command failed, because the setter
caches before ACK. A future implementation must track requested/acknowledged
state separately instead of copying that behavior blindly.

The targeted indirect-call scan used 53.8 MiB peak without swap, under the same
256 MiB/50%-CPU/40-second limits. No camera traffic or runtime changes occurred.

## Still blocked

The Java BuiltIn command catalog establishes the name/number but not the strategy
enum's values or meanings. The direct B/BL scan found no caller, but the virtual
startup replay above is now established. Its input is the cache, not a named enum
constant, so it does not establish supported values or their meanings. No value should
be guessed from speed presets, integer range or the setter's lack of validation.

Next evidence needed: actual enum declaration or a traced caller assigning a value
with known semantics, followed by response identity mapping and a reviewed camera-3
playback test once SD listing/transport works. Until then there is no strategy
builder, live sender or feature advertisement. This does not unblock SD listing
or native HD platform selection.

Inspection used sequential symbol-sized disassembly under 256 MiB memory,
zero-swap, 50%-CPU and 40-second limits. Highest reported process peak was
38.1 MiB; no emulator, SDK-wide decompile, decoder or network probe was involved.
