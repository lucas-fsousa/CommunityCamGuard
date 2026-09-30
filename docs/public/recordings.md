# Recordings: playback, downloads and resource usage

Media GET requests no longer start conversion.
The dashboard already requests preparation via POST. Custom API clients must also
POST preparation before polling/loading an incompatible uncached recording; a 409
does not mean conversion started. [Compatibility details](../internal/recording-get-boundary.md).

## What is stored

CCG records local MP4 segments without re-encoding the source video. MP4 is a
container; the video inside may be H.264 or HEVC/H.265. Browser support for the
container alone does not guarantee support for that codec/profile.

Archives and their index use server-side UTC, independent of the camera's display
timezone. The default segment length is 300 seconds. Downloads return the original
file, named with the friendly camera name followed by the original UTC-based
filename. Original resolution is not reduced for browser playback.

## How the dashboard plays an archive

1. Select a recording in **Recordings**. Repeated selection of the same ready item
   requests play without reloading it or discarding the seek position.
2. For HEVC, if the browser reports probable codec support, CCG tries the original
   MP4 without creating a conversion job. This capability hint is not a guarantee
   for every device, file or HEVC profile.
3. Otherwise, or if the native attempt fails, CCG prepares a complete H.264 MP4
   with duration/seek metadata. AAC audio is copied, not re-encoded. The first
   uncached opening waits for this conversion; subsequent views reuse its cache.
   Cache reuse requires the same source-file version. If the source grows or is
   replaced, an older conversion is not reused. The source-identity cache upgrade
   also requires one new on-demand preparation for older path-only cache entries;
   it does not delete original recordings or start a bulk conversion.
4. Native decoding/format failure, audio-only playback or no initial playback
   within 12 seconds causes at most one compatibility fallback. Explicit network
   errors and browser autoplay denial do not automatically start conversion.
5. If autoplay is blocked, use the video's native play control. There is no extra
   full-width play button. Leaving the view cancels its requests/timers; an already
   shared server conversion may finish for another viewer or later reuse.

The complete-file path retains seeking; the player does not stream an incomplete
conversion with a duration that grows a few seconds at a time. HTTP Range requests
serve requested portions without requiring the browser to download the entire file.
The live-camera player is a different pipeline from archive playback.

Delegated access includes revocable file/download delivery while retaining
Range/seek behavior; login and permission checks are deployed locally. See
[deployment evidence](../internal/delegated-access-activation.md) and
[interruption semantics and limits](../internal/recording-session-delivery.md);
already-delivered bytes cannot be recalled.

While preparing/starting a recording or waiting for playback data, a localized
spinner overlay covers only the player area (`rec-main`), never the recording list.
It does not intercept clicks. Playing, deliberate pause, failure, autoplay rejection
or leaving the view clears it; errors/autoplay guidance remain discreet text below
the player. Reduced-motion preferences disable spinner animation. This feedback
does not speed up conversion or create any additional media process.

## Resource limits and settings

- Codec inspection permits at most two `ffprobe` processes per application process.
  Concurrent requests for the same unchanged file reuse successful metadata;
  failed inspections are not cached. Each probe has a ten-second execution timeout;
  waiting for admission can add latency under contention.
- One compatibility encoder per application process; up to three additional jobs
  may wait. Requests for the same archive share work. Saturation returns HTTP 429
  with `Retry-After: 5`; the dashboard explains that playback preparation is busy.
- Queue wait is bounded to 30 seconds and encoding to 600 seconds. FFmpeg
  decoder/encoder/filter threads are limited. These are not a hard per-job RAM cap;
  Docker container memory limits remain important.
- `PLAYBACK_CACHE_MB` defaults to 2048. Eviction removes derived files only.
  **0 means unbounded cache**, not disabled conversion.
- `PLAYBACK_PRETRANSCODE` defaults to false. Enabling it spends CPU preparing
  recent clips even before a viewer selects them, using the same encoder budget.
  Empty derived files are not considered ready; background inspection does not
  refresh a cached file's least-recently-used retention priority.
- `RECORDING_RETENTION_DAYS` defaults to **7** and deletes older original footage.
  **0 keeps originals indefinitely**, but recording can pause when storage fills.
  The disk monitor and retention cleaner are separate policies.

The cache cap has a deployed primary-only runtime override API
([implementation](../internal/runtime-settings.md)); the other settings above
still come from `.env`/environment. The [settings tab](settings.md) exposes the cache override.
Environment changes require application recreation/reconfiguration;
browser reload alone does not update service settings. Never share the `.env` file.

## Troubleshooting and validation limits

If retrieving the recording list takes longer than 15 seconds, the dashboard
aborts that request and shows a list-specific error. Click **Search / Buscar** to
retry. This timeout is separate from video preparation and does not cancel a
recording or start another encoder. Changing filters or leaving the view also
cancels the obsolete list request.

An initial preparation delay is expected for an uncached compatibility conversion.
On the prototype, five-minute 1080p clips took roughly 18–22 seconds to convert.
A separate native HTTP check returned ready in 159 ms without conversion. These
are specific backend observations, **not a promised time to first visible frame**.
Download may feel faster because it bypasses conversion and uses the local player's
codec support.

If playback fails, note the build ID, browser/device, whether the issue is first
opening or subsequent playback, and whether native play/seek works. Do not post
recordings, camera identifiers, cookies, keys or private archive paths in public
issues. Backend logs contain content-free `playback_prepare` outcome/reason and
queue/encode timing events for conversion jobs; they do not measure browser decoding.

Tests cover API Range/authentication and an isolated Chromium run of compatible
playback, native-error fallback and seeking. Successful **native HEVC playback on
desktop/mobile**, default autoplay policy and full-dashboard UX still need broader
validation. See [technical evidence](../internal/recordings-native-playback.md),
[measurements](../internal/recordings-playback-measurements.md) and the
[roadmap](../../ROADMAP.md).
