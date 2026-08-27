# 0019 — Progressive first playback with seekable cache promotion

**Status:** superseded by [ADR 0021](0021-seekable-first-recording-playback.md) · **Date:** 2026-08-17

## Context

The recorder preserves each camera's full-resolution HEVC stream without continuous encoding.
Browsers need H.264, and the original playback endpoint synchronously converted an entire segment
before returning its first byte. With five-minute 1080p segments, a measured 216-second clip took
41.46 seconds to prepare. Direct download was immediate because it bypassed that conversion.

Recording the already-transcoded live variant would make future clips browser-native, but would
trade away the original stream, multiply storage bitrate and couple archival recording to the live
encoder. Continuously warming every segment would consume CPU even when nobody reviews footage.

## Decision

Keep the HEVC originals and bounded H.264 cache. For the first uncached view:

1. Start one shared FFmpeg encode per source segment.
2. Write fragmented H.264 MP4 and tail it to every current viewer as it grows.
3. After encoding, remux that same file with stream copy into the existing faststart cache.
4. Let the browser poll playback status and swap to the seekable cache at its current position.

The encoder continues if a viewer leaves, so paid work still warms the cache. Later views use the
normal range-capable `FileResponse`. Original downloads remain byte-for-byte unchanged.

## Consequences

- First picture arrives after the first encoded fragment/keyframe instead of after the complete
  five-minute conversion.
- Duration and arbitrary seeking may be incomplete only during the short progressive phase; the
  frontend upgrades the same session as soon as the faststart cache exists.
- Concurrent clicks cannot create duplicate encoders for the same segment.
- Encoding still costs CPU. This change removes idle waiting and duplicate work; hardware
  acceleration or shorter configured segments remain optional deployment levers.
