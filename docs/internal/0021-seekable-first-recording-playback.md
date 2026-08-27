# 0021 — Seekable-first recording playback with asynchronous preparation

**Status:** accepted · **Date:** 2026-08-27

## Context

ADR 0019 streamed a fragmented H.264 preview while converting an archived HEVC segment. It reduced
time to first picture, but the browser only knew about fragments already produced: duration grew in
small steps and the reviewer could not seek ahead. Physical use confirmed this was a worse failure
mode than waiting explicitly for a review-ready artifact.

The HEVC archive must remain untouched: it preserves source quality, keeps continuous recording
cheap, and uses far less storage than the browser H.264 representation.

## Decision

Prepare playback asynchronously, but attach the browser player only to a complete H.264 faststart
MP4:

1. `POST /recordings/prepare` starts one shared background encode for an uncached segment.
2. The dashboard polls `/recordings/playback-status` and displays a clear preparation message.
3. `/recordings/file` serves only a cached, seekable artifact (or an already browser-native source).
4. The background job continues after navigation and atomically promotes its output into the
   existing bounded LRU cache.

## Consequences

- Duration and arbitrary seeking work from the first displayed frame.
- First playback of an uncached HEVC clip waits for conversion; subsequent playback is immediate.
- Concurrent viewers do not duplicate encoding, and no new camera connection is opened.
- Original recordings and downloads remain full-resolution HEVC.
- ADR 0019 is superseded; fragmented progressive playback is not used for archived review.
