# 0004 — Crash-safe recording via fragmented MP4

**Status:** accepted · **Date:** 2026-07-27

## Context

The 24/7 recorder writes each segment with `ffmpeg -c:v copy` (remux, near-zero CPU). Written as
**plain MP4**, a segment's `moov` index is appended only on clean finalize — it sits *after* all the
media. So a hard kill (ffmpeg crash, or the whole host going down — which is exactly how an earlier
session died) leaves the in-progress segment with **no `moov` → completely unreadable**: the entire
current chunk is lost, not just its tail.

## Decision

Mux each segment as **fragmented MP4**, via one option on the segment muxer
(`recording/recorder.py`): `movflags=+frag_keyframe+empty_moov+default_base_moof`. The `moov` goes
up front and the media is a chain of self-contained `moof`/`mdat` fragments flushed on every
keyframe.

## Consequences

- An abrupt kill leaves the segment **playable up to its last flushed fragment**, not destroyed.
  Verified: a plain-MP4 segment truncated to 40% → `moov atom not found` (unreadable); a fragmented
  one truncated to 40–55% → valid duration, plays to the last good fragment.
- Everything else is unchanged: native `<video>` playback, `-c:v copy`, per-segment seek, the
  day/hour layout and the SQLite index.
- **Rejected:** MPEG-TS segments (most truncation-robust) — `.ts` doesn't play natively in a
  `<video>` tag, forcing HLS/transmux; fMP4 gives the same crash-safety while staying browser-native.
