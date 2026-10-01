# Recording playback: lifecycle repair (2026-09-23)

## Prepared-media startup deadline — 2026-09-27

A ready server response is not proof that the browser started playing. A pending
`play()` promise without `playing` or `error` could leave the loading overlay up
indefinitely. Prepared H.264 playback now has a 30-second startup deadline; expiry
detaches the source and offers a localized, explicit row-selection retry. It does
not automatically enqueue another conversion. Playing, pause, replacement and
disposal cancel the deadline; attempt ownership rejects late promise results.
Native HEVC retains its separate 12-second fallback deadline.

The Node lifecycle suite and 41 focused Python tests passed (81.6 MiB peak,
zero swap). No browser/camera/container was started. This bounds a code-level
failure, not a claim of real-browser homologation or faster uncached conversion.

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
reports failures. Autoplay rejection shows a small localized status message;
play remains available through the video's native controls.
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

### Autoplay UI regression correction

The user reported an oversized ready/play button even without ready media. The
global `button { display: inline-flex }` rule overrode the HTML `hidden` attribute
on the extra retry button. That duplicate button has been removed entirely.
Only actual autoplay rejection displays the small status message; native video
controls handle manual playback. Stopping playback also clears stale status.
The fake-media harness did not exercise CSS/layout and missed this regression.
Updated lifecycle tests cover native-play recovery; a static view regression
checks that no duplicate autoplay button is created. Real-browser verification
remains pending; this fix does not remove uncached transcoding startup time.

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

## Scoped loading overlay — 2026-09-24

The controller exposes an optional state callback; the recordings view owns a
localized spinner overlay confined to `rec-main`. It displays during preparation,
startup and media `waiting`, and clears on playing, intentional pause, failure,
autoplay denial and disposal. No new request, conversion or timer is introduced.
The overlay uses explicit `[hidden]` CSS, `role=status`, reduced-motion support and
`pointer-events:none`; the recording list and native controls remain usable.

Node contracts cover transitions/cancellation and existing playback behavior.
Real Chromium component checks with production view/CSS passed at 1280×900 and
390×900, confirming initial hiding and containment outside the list; screenshots
were inspected. An isolated five-second H.264 sample also passed actual playback,
seek/reselection and forced native-error fallback, asserting loading clears after
playing/disposal. These are not long-recording/mobile-device homologation.

Browser runs were sequential in 512 MiB/no-swap, 75% CPU cgroups: peaks 456 MiB
desktop, 176.1 MiB mobile, 236 MiB media lifecycle; all exited. Optional reproduction:
`scripts/check_recording_browser.py --recordings --browser PATH --width 390
--screenshot temp/recordings-overlay-mobile.png` inside the same resource limits.
The existing `--fixture` mode checks actual playback. No camera/production recording
was requested. Frontend bind mounts serve this after reload, without app recreation.

## Failed play attempt cleanup — 2026-09-30

A rejected `video.play()` promise (other than autoplay denial or deliberate pause)
previously showed an error but left the selection marked ready. Re-selecting that
row could reuse a failed media source instead of starting a fresh attempt. Media
error events also left the source attached, allowing subsequent events to overwrite
the failure feedback.

Both paths now invalidate the attempt, cancel startup timers, detach the media
source and clear loading. A single explicit row selection can prepare/load again;
there is no automatic request, conversion retry or new UI control. Late events
before retry cannot overwrite the error. Native decode failures still use the
existing one-shot compatibility fallback; autoplay denial and intentional pause
retain their source and seek position. This is a demonstrated lifecycle defect,
not evidence that it caused all earlier production stalls.

Node contracts cover unsupported/network/interrupted play rejections, media-error
events, late-event suppression and single-click recovery, alongside the existing
autoplay, pause, native fallback and selection cancellation cases.

The integrated production-view Chromium fixture also passed at 375×667 using the
existing local five-second H.264 file: playback, seek, same-row resume, rapid switch,
download isolation, cleanup and list timeout/retry. It ran in a 512 MiB/no-swap,
75%-CPU cgroup and reached that memory cap (zero swap); no additional browser run
was launched. First-frame 715 ms is a fixture observation, not production latency.
New rejection races are covered by Node; this browser run checks normal-flow
regressions, not physical mobile or authenticated production failure recovery.

## Post-start buffering deadline — 2026-10-01

The startup watchdog did not cover a `waiting` event after successful playback;
this could leave the spinner indefinitely if the browser emitted no media error.
Once playback has started, waiting now owns one 30-second timer. Repeated waiting
or timeupdate events without a position advance cannot renew it. Actual forward
progress outside an ongoing seek renews the observation window; playing, pause,
end, source replacement, fallback and disposal cancel it. A timer identity plus
attempt/selection checks reject already-queued stale callbacks.

Exhaustion clears loading and detaches the source with localized retry guidance,
without requests or automatic conversion/fallback. Explicitly reselecting the
same failed row restores its saved position after metadata arrives. Selecting
another row does not inherit that position. This is recovery from prolonged
buffering, not a transport-performance fix or a detector of every frozen frame.
Background-tab timer throttling can delay enforcement. Node contracts exercise
deadline, no-progress/seek events, resume position and cancellation races; no new
real-browser or physical-camera validation is claimed for this change.
