# Native archive playback — 2026-09-24

## Decision and scope

MP4 is a container, not a guarantee of HEVC decoding. Cold full-file conversion
took 18–22 seconds in the documented measurements. Prefer original HEVC delivery
for clients with a positive codec-specific capability hint; keep the existing
bounded H.264 compatibility cache for other clients and failed native playback.
No camera/driver capability changes, live streaming changes or audio conversion.

The browser checks `canPlayType` for MP4 with `hvc1`/`hev1` plus AAC (`mp4a.40.2`).
At least one result must be `probably`; empty/`maybe` uses the compatibility path.
This is deliberately only a hint: it does not identify the source's exact HEVC
profile, level, sample-entry tag or actual hardware performance. The
[HTML media specification](https://html.spec.whatwg.org/multipage/media.html#dom-navigator-canplaytype)
defines capability results as confidence levels, not proof of successful playback.
Actual browser/device homologation remains required; conservative hints may miss
devices which could decode the source.

## Protocol and fallback

- Authenticated `POST /api/recordings/prepare?path=...&native_hevc=true` checks the
  cached/probed source codec. For HEVC it returns `original: true, ready: true`
  without starting ffmpeg, even if another viewer has a compatibility job/cache.
- `GET /api/recordings/file?path=...&original=true` serves the original through
  FileResponse with Range support and unchanged path/authentication checks. It
  never starts encoding. This is a preference, not a new permission.
- Existing clients/default requests retain the old compatible behavior. The
  frontend attaches the original only when the server explicitly selects it.
- Unsupported/decode errors, audio-only playback (`videoWidth === 0` on playing),
  or no initial playback within 12 seconds trigger one compatible preparation.
  Its existing shared queue/cache prevents a new encoder for each consumer.
- Autoplay denial and explicit network errors do not trigger conversion; user
  pause cancels the startup timer, native manual play re-arms it, and successful
  playback removes it. This is not a perpetual stall watcher. The timeout cannot
  distinguish slow startup/network from a silent decoder stall and may therefore
  cause an unnecessary but bounded fallback.
- Fallback preserves the current seek position when metadata becomes available.
  Source attempt numbers discard stale play-promise results. Navigation, selection
  changes and logout clean up timers/requests/listeners. No retry loop, duplicate
  play button, media buffering in JS or persistent browser capability cache.

## Validation

Fake-media Node contracts cover positive/uncertain/negative hints, native success,
decode/unsupported/timeout fallback, one-shot behavior, position restoration,
autoplay denial, network failure, pause/manual resume, audio-only rejection and
selection cleanup. Existing lifecycle and camera-controls tests remain passing.
ASGI tests check native preparation without encoding, default compatibility,
non-HEVC hints, original Range bytes despite a derived cache, unauthenticated
rejection and outside-root rejection for both endpoints. These test payloads are
synthetic bytes, not a browser decoding benchmark.

Remaining: test real HEVC first-play/seek on desktop/mobile with and without
native support; verify fallback visually and measure ready-to-playing time. Do
not mark recordings performance fully resolved from API or fake-media tests.
