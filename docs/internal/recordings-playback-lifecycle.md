# Recording playback: lifecycle repair (2026-09-23)

## Findings

Code inspection establishes two separate paths, not a single diagnosed browser
failure. Uncached HEVC archives are fully transcoded to a faststart H.264 MP4 before
playback. Download serves the original immediately; their startup times therefore
cannot be compared as if they use the same processing. Complete preparation keeps
duration/seeking available, unlike the previous partial fragmented preview.

The old view restarted preparation and reloaded the media on every row click,
including the selected row. It waited for `loadedmetadata` before requesting play,
installed per-selection listeners, and never disposed polling/player ownership on
navigation or logout. Polling while transcoding had no absolute deadline. These
are code-level lifecycle defects; no real-browser trace yet proves which one
caused the reported second-click symptom. Browser autoplay rejection remains a
separate legitimate outcome, especially after asynchronous conversion.

## Changes

`recording-playback.js` owns one selection, request cancellation and timers. Only
the current selection may attach a ready source. Play is requested immediately
after source attachment instead of waiting on a metadata callback; its promise
reports failures. A localized explicit play button handles autoplay rejection.
Repeated clicks during preparation do nothing; clicks on an already-ready item
request play without a new POST, reload or loss of seek position. The file URL is
stable instead of timestamp-cache-busted on every selection.

Leaving Recordings, rerendering or ending the dashboard session aborts requests,
clears timers, pauses/detaches the video and removes listeners. List requests also
use cancellation and generation checks. Preparation/status requests have a 15s
client timeout, and polling now has a 650s overall window including queueing. Cancelling browser requests
does **not** claim to terminate server encoding: a shared job can serve other users.

This initial frontend repair did not change server transcoding, archive retention,
Range handling, camera traffic or live streams. No automatic audio muting or
autoplay-policy bypass. The subsequent server budget change is described below.

## Verification and remaining work

Node lifecycle tests use fake media/network/timers, covering first play request
without metadata events, duplicate clicks, seek preservation, stale completions,
autoplay rejection/retry, polling, disposal and request timeout. This harness is
now a dedicated CI step. Existing camera-control Node contracts and 42 focused
Python frontend/playback/API tests passed. These are not browser homologation.

## Conversion budget follow-up

Inspection found per-file deduplication but no aggregate encoder limit. The warmer
also bypassed the shared job path. Both synchronous warming and HTTP preparation
now share one job registry and one process-local encoder lock. Admission is capped
at four jobs (one encoding, at most three waiting); each waits at most 30 seconds
for the encoder and runs at most 600 seconds. A full queue returns HTTP 429 with
Retry-After: 5; the UI explains congestion and leaves retry to the user. Waiting
jobs that expire fail without spawning ffmpeg. The warmer skips archive scanning
while jobs are present and cannot enqueue behind foreground work.

FFmpeg explicitly limits decoder, encoder and filter threads to one and disables
stdin. These are concurrency/thread bounds, **not a hard RAM limit** or a promise
that native libraries never create an auxiliary thread. They are per application
process, not a distributed lock across multiple server workers. Original recording
and live pipelines, faststart MP4, audio copy, resolution and downloads are unchanged.
Successful/failed jobs log content-free outcome, queue_ms and encode_ms. Cleanup
always releases registry ownership even if deleting the temporary artifact fails.

54 focused Python tests passed, including concurrent fake encoders, queue admission,
queue expiry, warmer sharing, worker-start failure, HTTP 429, cache/faststart and
existing API contracts. Both Node suites, Ruff and Mypy (191 files) passed.
A **synthetic 2-second 320×180/15fps HEVC** file converted with the production
command in **116 ms**; ffprobe reported H.264, 30 frames and 2.000 seconds. Every
benchmark subprocess was limited to 512 MiB address space, 15 CPU seconds and a
wall timeout. Artifacts are in a small ignored temp directory. This checks command
compatibility only; it does not measure actual-camera latency or prove browser play.

Next: measure uncached/cached preparation, first-byte and browser startup on real
bounded samples, audit repeated ffprobe work and server Range/seek behavior, and
validate first-selection/navigation on desktop/mobile. Serial conversion can make
queued requests wait longer; the purpose here is preventing resource contention,
not claiming faster individual encoding. No continuous pretranscoding was enabled,
and no container deployment or browser validation was performed.

Real-file conversion and HTTP Range follow-up:
[measurements and remaining startup tradeoff](recordings-playback-measurements.md).
