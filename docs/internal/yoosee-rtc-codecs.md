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
