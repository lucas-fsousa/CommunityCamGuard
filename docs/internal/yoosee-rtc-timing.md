# Yoosee RTC receive timing — pinned SDK 6.45

Status (2026-10-08): static receive-path evidence establishes a **microsecond
media time base**, not a Unix epoch. No live decoding, clock synchronization,
capability grant or camera command was enabled by this analysis.

Binary provenance and the receive/codec chain are recorded in
[RTC codec evidence](yoosee-rtc-codecs.md). Addresses below refer to that pinned
ARM64 `libgwplayer.so`, not host FFmpeg or another APK version.

## Actual demuxer receives the queued packets

The secondary StreamingIO vptr is concrete object +8, pointing at vtable
`0x3529b8` +0xc0 = `0x352a78`. Dynamic relocations identify:

| Secondary slot | Relocation | Target |
| --- | --- | --- |
| +0x10 | `0x352a88` | audio read thunk `0x27e9bc` |
| +0x18 | `0x352a90` | video read thunk `0x27e9c4` |

Both thunks subtract eight from `this` and branch to the concrete packet readers.
`DemuxerImpl::readAVPacket` (`0x193084`, 2872 bytes) checks the resource's
StreamingIO pointer at `0x1930ec`, then adjusts it by +8 at `0x193108`.
It selects slot +0x10 for representation kind 1 and +0x18 otherwise, calling
at `0x193120`. The resulting ResPkt packet pointer is transferred to RawPacket
at `0x193130–0x19313c`; -11 takes a separate not-ready branch. The two generation
counters are reordered at `0x193670–0x193688`, not interpreted as timestamps.

This links the previously traced untouched queue output to the actual demuxer,
rather than assuming a similarly named capture/input interface is involved.

## Receive representations explicitly use microseconds

`open_stio_stream` (`0x19d0e8`) calls `open_internal_pb` at `0x19d22c` after
constructing a StreamingIO-backed AVIO context. In `open_internal_pb`
(`0x19db88`, 4364 bytes), the stream loop reads codec parameters at
`0x19e3b8` and dispatches media type 0 (video) / 1 (audio).

After constructing each new representation, both branches build the 64-bit
constant `0x000f424000000001`. Stored little-endian, this is the rational pair
`{1, 1000000}` at AVStream +0x20:

| Receive branch | Rational store | Read numerator/denominator | Cached ratio store |
| --- | --- | --- | --- |
| Video | `0x19e664` | `0x19e670` | `0x19e680` |
| Audio | `0x19e8fc` | `0x19e908` | `0x19e924` |

Each branch converts numerator and denominator to doubles and divides them.
The result is stored at shared allocation +0x90, equivalently representation
+0x78 because its concrete object begins at allocation +0x18. This is the
same ratio loaded by the receive consumer at `0x1938e0`, which multiplies
the packet's PTS by it at `0x193920` for reporting.

Together with the unchanged incoming PTS/DTS storage and queue handoff, this
establishes the SDK's microsecond interpretation for this receive path. It does
not establish that every camera emits well-formed/monotonic values, that resets
share an origin, or that other transport modes use this contract. Preserve raw
integers and session/generation boundaries; do not silently normalize timestamps
or use floating-point seconds as the stored source of truth.

## UTC is separate metadata

The receive caller invokes `parse_frame_time` at `0x1937c0` before
`processAVPacket` at `0x193818`. `parse_frame_time` (`0x19450c`, 388 bytes)
looks for packet side-data type 13, unpacks a dictionary, then reads the key
`frame_time` using `strtoll`. After a numeric-range check, it stores this value
in RawPacket +0x20 and logs `pkt.utc_ms`.

For nonnegative PTS it computes an offset using
`(frame_time / 1000 / representation_time_base) - PTS` at
`0x1945d4–0x19460c`. This is a separate wall-clock association, not evidence
that PTS itself is epoch time. No provenance for producing that side data from
RTC records has been established, nor has its numeric range policy been adopted.
Do not derive recording directory dates from it; server UTC remains authoritative.

## Conditional correction after receive

`processAVPacket` (`0x194120`, 1004 bytes) maintains per-stream history.
With the StreamingIO pointer present, a non-sentinel PTS and nonzero duration
skip its `correct_pkt_time` call (`0x194290–0x1942b0`). Otherwise, when its
duration-probe state is ready, it invokes that helper at `0x1942d8`. The video
path may establish duration from a live packet or `FPSProbe::probe_frame_duration`
at `0x19445c`; cached-packet repair is a separate call at `0x1944c4`.

`correct_pkt_time` (`0x1957fc`, 748 bytes) only edits a video representation
when its tracked duration is nonzero (`0x195904–0x195914`). It can replace
sentinel/missing PTS or DTS and, depending on a codec-parameter flag, regressions
with previous time + duration, or zero when no previous time exists. Stores occur
at `0x19595c` (PTS), `0x1959b4` (DTS) and `0x195acc` (duration). The exact
codec-flag semantics and duration heuristics are not yet homologated.

Thus **queue preservation does not imply end-to-end preservation** in the vendor
player: later stages have conditional timeline repair. This does not explain a
current dashboard stall (its live pipeline is RTSP/go2rtc), and copying those
heuristics without actual malformed/discontinuous fixtures would hide errors.
Keep the offline parser lossless and its PTS raw.

## Validation and remaining work

All inspection was static and sequential, capped at 128 MiB/50% CPU/no swap;
the new targeted reads peaked at 38.5 MiB. No proprietary library was executed.
The descriptor/helper commit `de786b0` passed exact-SHA CI run `37724951642`.

Next: inspect `PacketPtrMake` (`0x19f3d4`, 1596 bytes), cached-packet repair and
downstream decoder submission for further timeline rewriting, then validate framing, codec and timing
against a provenance-checked real record. A microsecond contract alone does not
authorize live native streaming. Authenticated relay ownership, platform/mode
verification, teardown and per-device capabilities remain independent gates.
