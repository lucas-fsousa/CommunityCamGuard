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

## Final wrapper and cache repair

`PacketPtrMake` (`0x19f3d4`, 1596 bytes) adopts the RawPacket's existing packet
pointer into the output wrapper at `0x19f494`. It copies representation +0x78
to AVExtInfo +0x160 at `0x19f59c`; it does not itself rescale AVPacket PTS/DTS.
The video metadata path prefers positive RawPacket +0x20 (the UTC association
above). Otherwise, if a nonzero demuxer clock offset and a packet exist, it
computes `(PTS + offset) * time_base * 1000` at `0x19f960–0x19f998`, storing
the integer result in AVExtInfo +0x28 at `0x19f7f4`. Without either source this
metadata value remains zero. These are wrapper metadata, not AVPacket PTS stores.

`correct_cache_pkts_time` (`0x19510c`, 1776 bytes) iterates representations,
drains cached packets via `cache_front(true)` at `0x195288`, calls
`correct_pkt_time` at `0x1952bc`, then invokes video duration repair at
`0x1954ec` before replacing the cache at `0x195654`. Its `av_rescale_q` at
`0x195548` converts the first cached packet's PTS to microseconds to set the
format context's start time, not to rewrite each packet's time base.

`repairVideoPacketDurations` (`0x195ff0`, 1796 bytes) requires video, valid stream
codec parameters, codec-parameter +0x60 equal to zero, and a nonempty queue.
For neighboring non-null packets it compares **both generation fields** at
RawPacket +0x18/+0x1c (`0x19611c–0x196138`). Only a same-generation positive
next-PTS minus previous-PTS difference replaces the previous packet's duration
at `0x196150`. Otherwise, an existing positive duration survives; a missing
duration falls back to tracked duration or one tick (`0x1961b8–0x196220`).
The final packet uses the same fallback (`0x196424–0x196490`). No PTS/DTS store
occurs in this duration-only helper. This is evidence for preserving generation
boundaries, not permission to synthesize durations across a reconnect.

## Decoder submission boundary

`AVDecoder::sendPacket` (`0x1636dc`, 1196 bytes) carries the same packet pointer
and copies AVExtInfo before virtual submission at `0x163948`. A concrete
`VideoDecoder::sendPacket` path (`0x17adf8`, 2008 bytes) submits at `0x17b290`
via `avcodec_send_packet`. It may first clone the packet and trim payload
data/size (`0x17b0ec–0x17b0f8`), and may reject continuity before submission.
Those are not timestamp rescaling operations.

`check_poc_continuity` (`0x17ab28`, 720 bytes) parses NAL slice information,
tracks a learned picture-order gap, resets that history for a keyframe, and can
return a negative error on a discontinuity. The caller checks this at
`0x17b284`. This concerns compressed-picture continuity, not the RTC eight-bit
sequence field or a wall-clock timestamp. The log saying `skip duplicate pkt`
branches to submission, so its wording alone must not be used as proof of a
dropped packet. Other decoder implementations and the queue/run path are not
covered by this bounded check; no universal decoder behavior is claimed.

## Decoder run-loop checkpoint

`AVDecoder::run` (`0x164a74`, 9324 bytes) was inspected through targeted windows
after a streamed call-site listing, not whole-section decompilation. The loop
snapshots packet queues under a mutex (`0x164bd8–0x164cf8`). Empty-queue paths
at `0x164ec8` / `0x164ee4` construct 10,000,000 nanoseconds and call
`sleep_for`: a **10 ms local wait**, not camera packet pacing or network RTT.
The packet path calls `popFront(false)` at `0x164e98`; the null-packet/flush
case separately invokes `pop_locked` at `0x164f54`.

The loop's metadata association is keyed by PTS (`unordered_map<unsigned long,
AVExtInfo>` at stack +0x200). Lookup at `0x1651d0–0x1652c8` detects an existing
key; after the `avoid duplicate pts` log, it increments a local counter and
**writes PTS + counter back to AVPacket +8** at `0x165330`. It then continues
to submission. This is a genuine later PTS rewrite, unlike the previously
inspected wrapper. This window does not update DTS and does not prove globally
unique timestamps, monotonicity or absence of cross-stream collisions.

After `sendPacket` at `0x165418`, success and -11 continue to receive processing;
other negative returns take the cleanup branch. Metadata is associated with the
submitted PTS at `0x165490–0x1654a0`. A per-stream list of association keys is
trimmed when its length reaches 42, repeating until it is at most 41
(`0x1654ec–0x165534`). **41 is a metadata-history limit**, not a proven frame
queue capacity, latency limit or camera-buffer size. After frame reception, the
-11 path logs `resend` at `0x1668ec` and returns to submission via `0x165338`.
This is local decoder backpressure handling, not retransmission to a camera.

`checkSkipFrames` (`0x167990`, 596 bytes) rejects an older seek generation at
`0x1679d4–0x167a38`. With a configured seek threshold, streams not already marked
ready reject earlier PTS until crossing that threshold, then enter the ready set.
Return zero takes the skip branch after the frame-side call at `0x1666dc`;
return one permits further state/listener/output checks. These are seek-generation
and timeline gates, not evidence of a generic live “drop to latest frame” policy.

Parser regression tests deliberately preserve duplicate video PTS, repeated and
backward grouped-audio PTS in wire order, zero/restarted timelines and all u64
boundary values (including the bit pattern the decoder uses as a signed sentinel).
No deduplication, epoch conversion or vendor PTS increment is copied into the
lossless RTC parser. Session ownership and generation validation remain caller-owned.

## Validation and remaining work

All inspection was static and sequential, capped at 128 MiB/50% CPU/no swap;
the new targeted reads peaked at 46.1 MiB. No proprietary library was executed.
The descriptor/helper commit `de786b0` passed exact-SHA CI run `37724951642`.

The timing checkpoint `e138118` also passed exact-SHA CI run `37725249416`.
The existing capture was already audited in [capture evidence](yoosee-push-capture-evidence.md);
it has not supplied an authenticated RTC fixture. Do not rerun that same negative
prefix inspection and present it as new interoperability evidence.

366 focused RTC tests passed (115.8 MiB peak/no swap under a 256 MiB/50%-CPU cap).
This validates synthetic parser contracts, not proprietary-library execution or
real-camera interoperability.

Next: validate framing, codec and timing against a provenance-checked real record.
The [render-boundary follow-up](yoosee-rtc-render.md) traces stale-output gates,
queue registration and a local clock helper. Actual presentation scheduling and
audio/video synchronization still require consumer/caller evidence.
A microsecond contract alone does not
authorize live native streaming. Authenticated relay ownership, platform/mode
verification, teardown and per-device capabilities remain independent gates.
