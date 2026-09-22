# Native video-definition encoding — offline SDK evidence, 2026-09-22

The first real native decode negotiated 640×360. This document maps a quality
selection mechanism; it does not claim HD works, selects the sensor's maximum
resolution, or leaves other sessions unaffected. No quality command was sent.

## Evidence

Existing decompiled Java `com/jwkj/iotvideo/player/constant/VideoDefinition.java`
defines LD=1, SD=2, HD=3, AUTO=7. `LivePlayer.setDefinition()` passes that value to
JNI; `setDefinitions()` passes an index/value map. Java's `BuiltInCmd` exposes
`SET_VIDEO_DEFINITION=5`, but the native platform-2 path uses another command.
The Java enum alone is therefore insufficient to build the wire request.

ARM64 `libiotvideomulti.so`, SHA-256
`b4d6f72168b666f80b08ebdd2eb210f57b5fd8199d8b375963f2568c5e96900a`:

| SDK location | Observed behavior |
| --- | --- |
| `LivePlayer::set_definition`, `0x1044e8` | Builds five map entries (indices 0–4) with the same enum value, then calls the map setter. |
| `LivePlayer::set_definitions`, `0x1041e0` | Reads `Connection::get_device_platform`; compares against 2 at `0x1042a0`. |
| `0x1042cc`–`0x1042e0` | Platform 2: packs each accepted slot as `definition << (3 * index)`, indices below 5; combines with OR. |
| `0x10437c`–`0x104390` | Other platform path: first map value minus 1, command 5, unsigned-byte specialization. |
| `0x1043d0`–`0x1043dc` | Platform 2: packed value unchanged, command `0x33`, unsigned-short specialization. |
| `_set_definition<unsigned short>`, `0x106360`–`0x106378` | Allocates two payload bytes and stores the 16-bit packed value (little-endian ARM64). |
| `_set_definition<unsigned char>`, `0x1069e4`–`0x1069fc` | Allocates/stores one payload byte. |
| `Connection::get_device_platform`, `0xf854c` | Uses cached platform or `iv_get_device_platform_version`, rather than deriving it from a display model name. |

The setter also short-circuits repeated values and can cache state without sending
in a connection-state branch. It calls local `APlayer::stop_record` when its local
recording is active. This is not proof that the camera's SD recorder is stopped;
do not conflate a vendor player recording with camera-side recording. Timing,
acknowledgement and effect still need to be traced before a live sender exists.

## Socket-free implementation

`p2p/video_definition.py` returns only the command ID and its argument bytes:

| Request | Command | Argument bytes |
| --- | --- | --- |
| Legacy HD | `0x05` | `02` |
| Platform-2 slot 0 HD | `0x33` | `03 00` |
| Platform-2 five slots HD (SDK uniform setter) | `0x33` | `db 36` |
| Platform-2 five slots AUTO | `0x33` | `ff 7f` |

This is **not a complete BuiltIn/KCP packet or callable control**. It has no
socket, credential loading, driver registration or API exposure. Unknown platform
is rejected (no legacy guessing); use authoritative metadata at eventual dispatch.
Only platform 1/2, explicit indices 0–4 and the four known enum values are accepted.
Conflicting per-slot requests on platform 1 are rejected rather than silently
taking the first entry as the SDK does. Unspecified packed slots are zero; their
camera-side interpretation is not established. Do not assume slot indices refer
to separate physical cameras or promise zero means "leave unchanged".

24 focused synthetic cases passed, covering all enum payloads, uniform/sparse
packing, ordering, unknown platform, invalid indices/values and legacy ambiguity.
Ruff and Mypy (190 files) passed. Full-suite verification remains the commit CI;
no repeated full local Docker suite was needed for this pure codec step.

## Next

Trace the initiating live-view userdata/default quality and the BuiltIn response
callback, framing and correlation; determine whether selecting HD before INIT can
avoid a mid-stream profile transition. Use the exact camera-3 platform and only
one reviewed bounded live attempt after tests/CI. Verify actual encoding dimensions
and independent decoding, not transport ACK alone. Keep changes in the Yoosee
driver and retain generic maximum-resolution-first / single-source fan-out policy.

Live decoding milestone: [first live decode](native-av-first-live-decode.md).
