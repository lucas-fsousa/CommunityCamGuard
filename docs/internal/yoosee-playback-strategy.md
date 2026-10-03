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

## Still blocked

The Java BuiltIn command catalog establishes the name/number but not the strategy
enum's values or meanings. A bounded direct B/BL cross-reference scan found no
caller of this setter; virtual/indirect callers are not excluded. No value should
be guessed from speed presets, integer range or the setter's lack of validation.

Next evidence needed: actual enum declaration or a traced indirect caller with
known semantics, followed by response identity mapping and a reviewed camera-3
playback test once SD listing/transport works. Until then there is no strategy
builder, live sender or feature advertisement. This does not unblock SD listing
or native HD platform selection.

Inspection used sequential symbol-sized disassembly under 256 MiB memory,
zero-swap, 50%-CPU and 40-second limits. Highest reported process peak was
38.1 MiB; no emulator, SDK-wide decompile, decoder or network probe was involved.
