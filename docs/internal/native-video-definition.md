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

## Startup and reply callback evidence (follow-up)

Bounded symbol disassembly of the same binary establishes these additional facts:

* `LivePlayer::set_opt_conn_params`, `0x108fcc`–`0x108fd4`, passes
  `this + 0x50`, length **32**, to `Connection::set_req_userdata`.
  That function (`0xf80c4`) copies the bytes into connection-owned storage.
  Consequently the legacy definition at player offset `0x50` occupies userdata
  byte 0; the packed definition at `0x67` occupies userdata bytes 23–24.
* The shared-pointer constructor at `0x103b6c` places the player at allocation
  offset `0x18`. It initializes the legacy field to 1 (`0x103c1c`), and copies
  the low 16 bits of route offset `0x1c` to the packed field (`0x103c30`).
  This is route-dependent initial state, not proof of a universal HD default.
  `update_connect_route` (`0x104a94`) instead loads **one byte** from that route
  offset and writes it as a halfword into `this + 0x67`; do not assume this path
  preserves all five packed slots.
* Our current `media_protocol.build_av_init` default writes legacy 1 and packed
  `0x0012`. Under the mapped enum encoding these are legacy SD and packed SD in
  slots 0 and 1, with zero in slots 2–4. This is consistent with the observed
  640×360 sample, but does **not** prove which field the camera consumed or what
  dimensions each profile yields. The production/default bytes remain unchanged.
* The unsigned-short setter's reply lambda at `0x107314` compares vector begin
  and end (`0x107338`–`0x107344`). Empty payload takes its success path. Otherwise
  only first byte `0xff` takes the `not support` failure path
  (`0x107348`–`0x107350`, `0x1073ec`). Other first-byte values also take success.
  Success caches the **requested** value (`0x1073a0`–`0x1073c0`) and invokes the
  callback without an error; it does not read negotiated dimensions from a reply.

These are application-callback semantics **after** SDK message dispatch, not a
rule allowing arbitrary UDP packets, empty datagrams or transport ACKs to confirm
quality. Message framing, response identity and pending-request correlation still
need tracing before a sender/response parser can be safely implemented. A future
implementation must separate transport receipt, application acceptance and actual
decoded resolution. Never mark HD supported merely because this callback succeeds.

No camera traffic, deployment, decoder process or SDK-wide scan was needed for
this follow-up. Individual disassembler processes were limited to 256 MiB address
space and 20 CPU seconds. Host swap was already heavily occupied, so no build or
full test container was started.

## Startup preparation and wire locations

`with_startup_definitions` now prepares an immutable copy of a supplied, reviewed
32-byte userdata template. It reuses the strict platform/enum encoder and changes
only byte 0 (platform 1) or bytes 23–24 (platform 2), preserving all other bytes.
It rejects missing/wrong-size/mutable templates and does not generate defaults,
infer a platform, open a socket or change any production caller. In particular,
it does not overwrite the other platform's field "just in case". Sparse maps
still zero unspecified packed slots; they are not read-modify-write updates.

Additional bounded disassembly of the same SDK:

| Location | Evidence |
| --- | --- |
| `iv_init_frm_CALLING`, `0x202664`–`0x2026d0` | Optional 48-byte extension is copied to A4 offset `0x80`; its last 32 bytes come from channel offset `0x150`, so userdata lands at A4 `0x90:0xb0`. The branch requires channel flags bit 20 (`0x202640`–`0x202648`). |
| `0x202748`–`0x2027b8` | A separate definition byte comes from channel `0x136`, with bit 7 conditional on `0x137` and bit 6 conditional on connection type 2. It follows the optional extension; do not treat it as an unconditional alias of userdata byte 0. |
| `iv_init_frm_AvStreamCtl`, `0x2015b8`–`0x201634` | INIT action 1 copies 32 bytes from channel `0x21c` to control-body `0x18:0x38`. Connection type comes from channel `0x10c` into body `0x10`. |
| `0x201700`–`0x201738` | ACCEPT action 2 uses a different source at channel `0x23c`; do not confuse response userdata with the request template. |

The native channel source offsets differ between A4 and INIT. Their assignment
chain still needs verification before claiming one supplied template reaches both
unchanged. Our current custom-userdata codec path permits **SD playback only**;
do not relax that guard or add the playback-only `0x40` definition bit to live
requests as a shortcut. The live default remains its captured byte sequence.

38 focused cases passed (existing encoder cases plus startup field preservation,
immutability, idempotence, invalid templates and sparse packing); Ruff passed.
Full-suite/type-check validation runs in commit CI, avoiding a local build or
full-suite container while host swap is nearly exhausted. No camera test or
production deployment was performed.

## Assignment chain closed; explicit live codecs

The pending source-offset question above is now resolved for this SDK binary:

| Step | Exact evidence |
| --- | --- |
| C++ userdata → C API argument | `Connection::Impl::connect` lambda `0xfbfac`–`0xfbfdc` copies the structure at Impl `0x94` to the argument passed to `iv_start_av_link`. Userdata at Impl `0xa8` consequently begins at argument offset `0x14`. |
| Argument → A4 metadata source | `iv_start_av_link`, `0x193bb0` and `0x193c1c`–`0x193c30`, copies `0x4c` bytes of that argument into channel `0x13c`; its userdata is therefore channel `0x150`, already traced into A4 `0x90:0xb0`. |
| Argument → separate definition byte | `0x193b5c`–`0x193b64` copies argument byte `0x14` to channel `0x136`. This establishes the previously unproven relationship to userdata byte 0 before the CALLING builder adds mode bits. |
| Argument → intermediate userdata | `0x193d34`–`0x193d54` copies 32 bytes from argument `0x14` to channel `0x114`. |
| Intermediate → INIT source | `iv_start_process_calling`, `0x201e7c` sets copy length 32; `0x201eec`–`0x201f14` copies channel `0x114` to channel `0x21c`, the source used by `iv_init_frm_AvStreamCtl`. |

The frame codecs now accept explicit connection type **1 (live)** as well as
**2 (SD playback)**. Both require exact integer types (no bool/float coercion) and
32-byte metadata. Live's separate A4 definition byte is userdata[0]; only playback
adds `0x40`. Existing no-argument packets remain unchanged. This supersedes the
earlier SD-only guard note; it does not enable a runtime override or default HD.

New offline tests decrypt broker/direct A4 frames and compare their complete
userdata with INIT for both platforms and all four qualities. They also verify
no playback flag on live, unchanged captured direct/INIT defaults, and invalid
connection types. **91 focused tests passed**, including existing media/session
and SD-playback-carrier regressions; Ruff passed. Commit CI covers the full suite.
No deployment or camera traffic occurred.

## Bounded route propagation (2026-09-23)

The internal `probe_av_route` accepts optional immutable `request_user_data` and
validates it as live metadata before creating a socket. The same bytes object is
passed to broker rendezvous, direct media setup and `probe_av_socket`; the latter
passes it through `AvHandshake` to `ReliableAvControl` for INIT only. The sender
builds the packet once, so retries preserve payload, sequence and timestamp.
START/CLOSE reject supplied startup metadata and retain their original bodies.
Omitting metadata keeps all captured defaults unchanged.

This is transport plumbing, **not an enabled HD diagnostic**. No configuration,
HTTP parameter, dashboard capability or operator handler invokes the override.
The existing server-side reviewed-camera gate remains unchanged. Platform
selection/provenance must be enforced by that policy before it supplies metadata;
the low-level transport cannot establish a platform from arbitrary userdata.

139 focused tests passed across route/probe/handshake/reliable controls and quality
codecs. New tests cover object-preserving stage propagation, real receive-loop
INIT/START/CLOSE behavior using fake sockets, byte-identical INIT retries, early
invalid-input rejection with sample cleanup, and route teardown. No camera action,
container restart or build was performed. Commit CI verifies the full suite.

## Next

**Live HD attempt is blocked as of 2026-09-23.** Review of the existing platform
parser, rendezvous/media collectors and private SD-investigation evidence confirms
that camera 3's prior bounded collection returned no authoritative E4. The stored
live-decode milestones establish decoded media, not the platform enum. A targeted
filename-only search of Frida logs for `iv_get_device_platform_version`,
`device_platform_version`, `get_device_platform` and `platformVersion` found no
matching logs; this is not an exhaustive claim that all captures lack E4.

The SDK's default registry value of 1 in the absence of E4 is not positive evidence
that this camera uses platform 1. Inventory flags, model/firmware strings and media
dimensions do not resolve the question. Do not add a configuration integer that
silently turns an operator guess into authoritative metadata. Next offline RE is
the E4-producing exchange or an independently proven equivalent; no identical
live collection retry or profile command was issued during this review. No user
action is required for that offline investigation.

Connect the operator-only profile selection to authoritative platform metadata,
camera-3 identity and decoder bounds before any HD trial. The immutable transport
path is now implemented and tested offline; the live override remains unused.
Do not expose generic arbitrary-userdata input in the dashboard. Trace BuiltIn
response framing/correlation separately before implementing mid-stream changes.
A pre-INIT HD request remains untested.
Use the exact camera-3 platform and only
one reviewed bounded live attempt after tests/CI. Verify actual encoding dimensions
and independent decoding, not transport ACK alone. Keep changes in the Yoosee
driver and retain generic maximum-resolution-first / single-source fan-out policy.

Live decoding milestone: [first live decode](native-av-first-live-decode.md).
