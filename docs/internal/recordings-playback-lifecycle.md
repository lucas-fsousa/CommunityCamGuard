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
client timeout, and polling has a 610s overall window. Cancelling browser requests
does **not** claim to terminate server encoding: a shared job can serve other users.

No server transcoding, archive retention, Range handling, camera traffic or live
stream behavior was changed. No automatic audio muting or autoplay-policy bypass.

## Verification and remaining work

Node lifecycle tests use fake media/network/timers, covering first play request
without metadata events, duplicate clicks, seek preservation, stale completions,
autoplay rejection/retry, polling, disposal and request timeout. This harness is
now a dedicated CI step. Existing camera-control Node contracts and 42 focused
Python frontend/playback/API tests passed. These are not browser homologation.

Next: measure uncached versus cached first-byte/preparation/play timings on bounded
samples, audit shared conversion concurrency/thread limits (current job sharing is
per-file, not a global concurrency cap), and test first-selection/seek/navigation on
real desktop/mobile browsers. Do not solve startup delay by buffering whole files
in JavaScript or enabling continuous pretranscoding on resource-constrained hosts.
Container deployment and physical/browser validation have not been performed here.
