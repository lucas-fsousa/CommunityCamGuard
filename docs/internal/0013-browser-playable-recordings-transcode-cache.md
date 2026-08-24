# 0013 — Browser-playable recordings via a bounded HEVC→H.264 transcode cache

**Status:** accepted · **Date:** 2026-07-28

## Context

Segments are recorded **HEVC** (`-c:v copy`, the zero-CPU point of 24/7 recording — ADR 0004), but
browsers can't decode HEVC in a `<video>` tag: the recordings player showed a black screen, and the
failed video track took the audio down with it. A recordings reviewer also needs to **seek**, which
needs the clip's real duration up front. And any derived cache competes with recordings for disk on a
box whose storage policy never deletes (ADR 0009), so it can't be unbounded.

## Decision

`/recordings/file` serves **H.264**. `recording/playback.py` transcodes an HEVC segment on first view
to a cached **faststart** MP4 (`moov` + real duration up front, so the browser can seek), served via
`FileResponse`; later views hit the cache (instant). Audio is `-c:a copy` (already AAC); H.264
segments are served as-is; the recorder is unchanged.

The cache is a **size-capped LRU** (`PLAYBACK_CACHE_MB`, default 2048; `0` = unbounded):

- Only **derived transcodes** are ever evicted — never a source recording (each entry is losslessly
  reproducible on the next view).
- **LRU by mtime**, refreshed on a cache *hit* via `os.utime` (read atime is unreliable on `noatime`
  volumes).
- Eviction runs on the **write path** only, right after a new transcode is promoted (`_evict(keep=…)`)
  — no background thread; the file about to be served is protected via `keep`.

## Consequences

- Recordings play and seek natively in the browser; the derived cache can't silently eat the disk.
- **Rejected:** progressive streaming (fragmented MP4) for playback — it starts faster but leaves the
  browser with no duration and no seek (a bogus ~25 s pseudo-duration), worse for review. (Note this
  is the opposite choice from *recording*, where fragmented MP4 is right for crash-safety — ADR 0004.)
- Deferred: background **pre-transcode** of new segments so the first view doesn't pay the latency
  (`PLAYBACK_PRETRANSCODE`, opt-in).

## Amendment — progressive first view (2026-08-17)

Real recordings are five-minute, 1080p HEVC segments. On the prototype host, converting only
216 seconds took **41.46 seconds**, during which the old endpoint returned no response body. That
made downloading the original feel dramatically faster and made FFmpeg compete with live view
while the browser displayed only a spinner.

The earlier rejection of fragmented playback still applies to the *final* review artifact: a
reviewer needs reliable duration and seeking. It no longer justifies withholding every byte until
the final artifact exists. The first uncached request now tails a growing fragmented H.264 MP4 from
one shared FFmpeg job. When encoding completes, the same output is remuxed with `-c copy` into the
faststart cache; no second video encode occurs. `/recordings/playback-status` lets the frontend
detect that transition and replace the progressive source while preserving `currentTime` and play/
pause state. Thus startup is progressive, while the steady-state player remains seekable.

Concurrent viewers of the same segment share one job. A browser disconnect does not cancel it: the
job completes the reusable cache. Temporary fragments disappear after the last reader closes, and
the existing bounded LRU policy remains authoritative for completed derived files.
