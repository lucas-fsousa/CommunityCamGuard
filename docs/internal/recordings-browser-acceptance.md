# Recording browser acceptance: gesture and long seek — 2026-09-28

The production playback controller was exercised in a real, muted Chromium process
against an isolated loopback fixture server. No dashboard login, production media
consumer or camera connection was opened. Each browser tree was capped at 512 MiB,
zero swap, 75% of one CPU, 128 tasks and a 65-second runtime; runs were serial.

## Autoplay rejection and recovery

The opt-in runner now supports `--autoplay-block`. It selects Chromium's
`document-user-activation-required` policy and an unmuted video element (the browser
process still has `--mute-audio`, so this does not play sound). The controller
received a real `NotAllowedError`, showed its existing compact press-play message,
cleared the loading state and did not request another preparation/conversion.

The runner then supplied one trusted CDP user gesture to reselect the same item.
Playback resumed, seeking and repeated selection preserved the source, and the
preparation count stayed at one. Runtime 5.415 s; peak 250 MiB, zero swap. This
checks the real browser policy path, not physical mouse/touch or every browser's
gesture rules. The default muted compatible/native-error-fallback cases also passed.

## Five-minute fixture and seeking beyond the loaded buffer

A pre-existing five-second H.264/AAC derived sample was looped into a **300-second,
4,595,021-byte faststart MP4** using `-c copy`, not re-encoding. Remuxing took 316 ms,
peaked at 69.2 MiB and ran under a separate 256 MiB/no-swap/50%-CPU cap. The private
generated artifact is in ignored `temp/recording-long.aUiptz/looped-five-minutes.mp4`.
This is repeated footage, not a new capture or a representative bitrate benchmark.

`--seek-seconds 240` passed for compatible-first and native-error fallback. To
exclude the trivial case where the whole small fixture was already downloaded,
`--throttle-kib 64` limited fixture response writes and disabled HTTP caching.
Before seeking, the player reported only **9 seconds buffered**. It sought to
240 seconds and resumed beyond 240.1 seconds without reloading on row reselection.
The first compatible run measured 2,133 ms to first frame and 1,456 ms for seeking.
The fallback case also passed, but shares the URL/browser session with the earlier
case and its faster timings are **not** independent cold-cache measurements.
The whole throttled test took 5.487 s and peaked at 248.2 MiB, zero swap.

The fixture server implements HTTP Range for this bounded file; it is not the
production file route. Production Range/auth/transfer cancellation remain covered
separately by ASGI tests and prior HTTP checks. These results do not certify large
HEVC cold conversion, full Recordings-page gestures, Android/iOS, slow disks or a
reverse proxy. They establish real controller behavior for decoded playback,
gesture denial/recovery, long duration and seeking outside the available buffer.

## Integrated Recordings view — 2026-09-30

The new `--recordings-playback --fixture <short-H264.mp4>` mode loads the production
Recordings view, list, player controller, loading overlay and stylesheet together.
Only the API metadata/preparation replies are simulated; Chromium actually decodes
the existing five-second file through the bounded local fixture server.

Desktop 1280×900 and narrow 375×667 runs passed first-selection playback, seeking
to two seconds, same-row resume without reload/repreparation, isolated download
clicks, switching past a pending preparation and rejecting its late response,
active-row consistency and source/overlay cleanup on leaving the view. Initial
first-frame measurements were 761/541 ms; these are fixture timings, not production
or cold-HEVC measurements. Peak browser memory was 359.9/236.8 MiB, zero swap.

The review found an unbounded list request (distinct from media preparation).
Production now aborts list retrieval after 15 seconds and shows a localized list
error instructing the user to search again. Superseding searches and navigation
clear the timer; stale responses cannot overwrite the list, and no automatic
retry is added. Node contracts run in CI. A final 375×667 browser run also passed
the timeout/manual-retry path using an accelerated test timer (234.7 MiB, zero swap).
No production recording, camera connection, conversion or server setting changed.

Still open: authenticated full-dashboard/proxy behavior, physical mobile browsers
and representative cold-HEVC preparation. Successful fixture playback does not
disprove the user's earlier production stalls.

## Repeating the checks

Use the same capped service command in [native playback](recordings-native-playback.md).
Pass `--autoplay-block` with a short H.264 fixture, or `--seek-seconds 240
--throttle-kib 64` with an existing >241-second H.264 fixture below 10 MiB. The runner
rejects uncapped cgroups and launcher scripts. It exits and cleans up its browser
group/profile after each run; nothing is left monitoring in the background.
