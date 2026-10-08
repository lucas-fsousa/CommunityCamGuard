# RTC codec enum translation evidence

Pinned input: SDK 6.45 APK `config.arm64_v8a.apk`, ARM64 `libgwplayer.so`,
SHA-256 `95f1348832ec56271dd0f2466d0c8c2fab19dfc390d93a25384918a097220e5e`.
Read-only static analysis; no proprietary binary execution or device traffic.

`AudioCodecID_to_AVCodecID` at `0x20f520` and its video equivalent at
`0x20f59c` search maps at `0x365a18` and `0x365a30`. Each compares an
eight-bit SDK code at node +0x20 and returns the AVCodecID at +0x1c; unknown
values return zero. Local code returns None rather than silently selecting
an invalid/default codec, and rejects lossy input conversions.

Initializer `0x20fe34–0x20ff24` copies six eight-byte audio pairs from
`0xfceb8` and appends `(0x1503c, 7)` using immediate instructions. It copies
four video pairs from `0xfcef0` and appends `(0xad, 5)`. Pair layout is
little-endian u32 AVCodecID, byte SDK code, three padding bytes.

| SDK code | Audio AVCodecID | Video AVCodecID |
| --- | --- | --- |
| 1 | 65543 | 27 |
| 2 | 65542 | 12 |
| 3 | 69643 | 88 |
| 4 | 86018 | 7 |
| 5 | 73728 | 173 |
| 6 | 69641 | unknown |
| 7 | 86076 | unknown |

The small offline `push_rtc_codecs` module exposes this numeric translation.
It deliberately does not infer codec names from remembered FFmpeg enum values,
parse an unproven descriptor field, select a decoder, or advertise capabilities.
The presence of a codec in this SDK is not evidence that a particular camera
supports it. Header field provenance still follows the separate
[RTC consumer evidence](yoosee-push-rtc.md).

Bounded map-reference scanning used 1 KiB instruction chunks, peaking at
31.5 MiB under a 128 MiB/no-swap/50%-CPU cap. Constructor and table reads
were separate sequential operations. No whole-section Capstone allocation.
Regression vectors cover all twelve pairs, unknown values, invalid media kind
and out-of-range/noninteger IDs. PTS units remain unverified.

Next: confirm codec names using pinned decoder descriptors, complete the
header-to-format field mapping, then verify time base and real-media fixtures.

## Header descriptor fields confirmed through format consumers

Combined with BasePlayer's entry-to-format stores, the named log arguments in
`VideoInputImpl::apply_codec_format` (`0x287f3c`, 464 bytes) confirm video
entry +4/+6 as u16 width/height, +8 as float32 frame rate, +12 as codec enum.
The format struct itself uses codec +0, pixel format +1, rate +5, width +9,
height +13. The SDK sets pixel format separately; it is not an extra proven
wire field in our descriptor parser.

`AudioInputImpl::apply_codec_format` (`0x2866dc`, 444 bytes) logs codec,
codec option, channels, bit width, sample rate and frame size from struct
offsets 0/1/5/9/13/17 respectively. BasePlayer's stores map these to entry
bytes +12/+13/+10/+11, u32 +4 and u16 +8. Entry byte +2 maps to
`max(0, value - 1)` in both kinds, **before** the consumer's audio index offset.

`push_rtc_formats` exposes immutable video/audio metadata objects and the
already pinned numeric AVCodecID mapping. Exactly twenty immutable bytes are
required. Unknown kinds are rejected; unknown codec values remain None.
Nonfinite FPS is rejected, but zero/low values are retained, not silently changed
to the SDK's default 15 FPS. These are reported metadata, not decoder-allocation
validation or proof that the camera supports any feature. Original bytes are
retained but excluded from object representations.

350 focused tests passed on 2026-10-04 (105.5 MiB peak/no swap, 256 MiB and
50%-CPU cap). Tests cover asymmetric field offsets, distinct index namespaces,
unknown codecs, malformed descriptors and both header record types.

Both AVInput configuration routines write a packed 1/1000000 rational. These
are capture/encoder input configuration routines; that alone does not establish
RTC receive-side PTS units. Keep `pts_raw` unscaled until the receive packet
path is traced. No live camera traffic, production caller or rebuild added.

### Receive-side timing route located

In libiotvideomulti, `BasePlayer::Impl` constructor `0x11a130` creates an
`IStreamingIO` at `0x11a1c8`, storing its shared pointer at +0x38. Its factory
boolean comes from route field +0x20 equal to 1; do not equate this with the
device-platform or encryption enums. This identifies the receive-side family
used by the previously traced virtual packet calls, unlike capture AVInput.

In libgwplayer, `StreamingIO::on_rcv_apkt` (`0x27c450`, 732 bytes) preserves
incoming x3 in x20 (`0x27c4c0`) and forwards it as x4 to `on_rcv_pkt` at
`0x27c604–0x27c610`. Separately it computes a duration as
`frame_size * 1000000 / sample_rate` (`0x27c5ac–0x27c5d0`), with fallback
60000 when either field is zero. This is receive-side microsecond-duration
evidence, but not yet sufficient to assert the incoming PTS scale without
checking common packet storage/output and concrete vtable bindings.

Next bounded targets: factory `0x27afac` (184 bytes), common `on_rcv_pkt`
`0x2805d4` (3144 bytes), `get_packet` `0x283dac` (1400 bytes), and video
receiver `0x27c72c` (1468 bytes). Do not copy unchecked 32-bit duration
multiplication or fallback into production. No new timing conversion enabled.

### Common packet storage and queue output inspected

`StreamingIO::on_rcv_pkt` (`0x2805d4`, 3144 bytes) saves incoming x4
(PTS) in x20 at `0x280608`, allocates via `av_packet_alloc`/`av_new_packet`,
and copies the payload. At `0x280658` it stores that same value at packet
+8 and +16 (the PTS/DTS pair); duration x6 is saved in x28 and written
at +0x40 at `0x28065c`. No timestamp rescaling occurs at these stores.
The queue stores the allocated packet pointer at entry +8, alongside two
32-bit generation counters (`0x280898–0x2808a0`, growth path
`0x280934–0x280940`). These counters are not packet timestamp fields.

`get_packet` (`0x283dac`, 1400 bytes) loads the queue entry at `0x2840a4`
into x26/x25, removes that entry and returns the same pair through `ResPkt`
at `0x28410c`. The packet pointer is not replaced or its timestamps rescaled
on this successful dequeue path. Generation changes and not-ready/flush cases
have separate branches; do not interpret every return as a media packet.
Listener callbacks after dequeue remain outside this limited proof.

This narrows the missing timing proof to the concrete interface bindings and
downstream time-base consumer. It does **not** turn `pts_raw` into a Unix
timestamp, prove wall-clock synchronization, or authorize a conversion in the
driver. Static inspection peaked at 33.6 MiB with no swap under the same
128 MiB/50%-CPU cap; no device traffic or runtime changes.

### Receiver demuxer names (not capability evidence)

`get_ff_avfmt` (`0x27e634`, 888 bytes) chooses a string and passes it to
`av_find_input_format` at `0x27e92c`. Its audio branches explicitly select:

| Audio SDK code | Input-format string | Evidence address |
| --- | --- | --- |
| 3 | `g726` | `0x27e898` |
| 4 | `aac` | `0x27e83c` |
| 5 | `amr` | `0x27e87c` |
| 6 | `adpcm_adx` | `0x27e8a4` |
| 7 | `opus` | `0x27e860` |

Codes 1/2 leave the string empty on this path. Video uses a relative string
table at `0x1030ec`, decoded below. These are **input-format selectors**,
not a substitute for verifying decoder descriptors, bitstream framing or actual
camera support. Keep unknown codec names unknown in production.

### Concrete receive-interface binding confirmed

`IStreamingIO::create(bool)` at `0x27afac` allocates a shared object and calls
`StreamingIO::StreamingIO(bool)` at `0x27aff0`. It returns the concrete object
pointer (allocation +0x18) at `0x27affc`, without a secondary-base adjustment.
The constructor at `0x27bbac` loads the vtable symbol through GOT `0x3578b0`
and writes the primary vptr as vtable +0x10 at `0x27bc38`.

Dynamic relocations resolve that GOT entry to `_ZTVN8gwplayer11StreamingIOE`
at `0x3529b8` (272 bytes). The primary slots are:

| Slot relative to primary vptr | Relocation address | Concrete target |
| --- | --- | --- |
| +0 | `0x3529c8` | `on_rcv_avhdr`, `0x27be88` |
| +8 | `0x3529d0` | `on_rcv_apkt`, `0x27c450` |
| +0x10 | `0x3529d8` | `on_rcv_vpkt`, `0x27c72c` |

This closes the concrete binding gap for BasePlayer's previously traced audio
and video virtual calls: the packet-storage evidence above belongs to the actual
receive implementation, not the capture-input family. It still does not prove
the downstream decoder time base or a wall-clock epoch. Keep PTS raw until that
consumer contract is verified. Bounded static reads used at most 41.1 MiB/no swap
under a 128 MiB cap; no proprietary binary execution or camera traffic.

### Pinned codec descriptor names — 2026-10-08

`libgwplayer.so` explicitly depends on `libavcodec_gw.so` (DT_NEEDED). The latter
ARM64 member in the same 6.45 archive is 3,415,296 bytes, SHA-256
`061277ef3e31b149489971a837f29c7393eb1d021e4ddfedce4e61bbb02cef51`.
This is the inspected dependency, not the separately bundled `libavcodec.so`
or the host's FFmpeg version.

`avcodec_descriptor_get` (`0x13f6d4`, 88 bytes) searches 517 entries at
`0x32ee38`, stride 48. Its comparator (`0x13f72c`) reads the 32-bit ID at
offset zero from both arguments. `avcodec_descriptor_get_by_name` (`0x13f768`)
loads each name pointer at +8 before `strcmp`. Resolving AArch64 RELATIVE
relocations for those pointers gives the following metadata:

| Kind | SDK enum | AVCodecID | Descriptor name | Descriptor address |
| --- | --- | --- | --- | --- |
| Video | 1 | 27 | `h264` | `0x32f318` |
| Video | 2 | 12 | `mpeg4` | `0x32f048` |
| Video | 3 | 88 | `jpeg2000` | `0x32fe88` |
| Video | 4 | 7 | `mjpeg` | `0x32ef58` |
| Video | 5 | 173 | `hevc` | `0x330e78` |
| Audio | 1 | 65543 | `pcm_alaw` | `0x3321f8` |
| Audio | 2 | 65542 | `pcm_mulaw` | `0x3321c8` |
| Audio | 3 | 69643 | `adpcm_g726` | `0x332978` |
| Audio | 4 | 86018 | `aac` | `0x3333f8` |
| Audio | 5 | 73728 | `amr_nb` | `0x333128` |
| Audio | 6 | 69641 | `adpcm_adx` | `0x332918` |
| Audio | 7 | 86076 | `opus` | `0x333ed8` |

The video input-format table at `0x1030ec` instead resolves SDK enums 1..5
to `h264`, `mpeg4`, `mjpeg`, `mjpeg`, `hevc` (string addresses `0xf45b4`,
`0xf3a46`, `0xeb7cf`, `0xeb7cf`, `0xf3067`). In particular, enum 3 has a
**descriptor/selector discrepancy**. Preserve the numeric mapping and record
the discrepancy; do not guess an MJPEG decoder or silently rewrite the enum.

The offline `rtc_codec_name` helper and format properties expose descriptor
metadata only. Unknowns remain None; invalid kinds/bytes are rejected as before.
A descriptor does not prove decoder availability, playable packet framing or
per-camera capability. No runtime decoder selection is enabled. Table inspection
peaked at 20.2 MiB and comparator inspection at 26.6 MiB, no swap, under 128 MiB.
Remaining gates include downstream PTS units/epoch and real-record interoperability.

Validation: 359 focused RTC tests passed (105.8 MiB peak, no swap, 256 MiB
cap), Ruff passed, and full-app Mypy passed for 245 files under a separate
384 MiB cap. No runtime wiring or decoder allocation was introduced.

### Receive queue wrappers

`read_au_packet` (`0x27e034`, 636 bytes) and `read_vi_packet` (`0x27e2b0`,
636 bytes) resolve the requested buffer under a mutex, then pass the original
caller-owned `ResPkt` to `get_packet` at `0x27e184` / `0x27e400`. Their successful
return paths release the temporary buffer reference but do not rescale or replace
the packet. Missing buffers return -11. These wrappers therefore do not supply
the missing receive time base; the next target is their decoder-side caller,
not capture-input time-base initialization. Inspections peaked at 35.9 MiB/no
swap under 128 MiB, without executing SDK code or contacting cameras.

Follow-up: the secondary-vtable/demuxer link and receive representation setup
now establish a microsecond media time base; UTC remains separate metadata.
See [receive timing evidence](yoosee-rtc-timing.md) for exact branches, limits
and remaining timeline-rewrite/real-record validation. Earlier sections describe
the narrower evidence available at their checkpoint; no runtime conversion is enabled.
