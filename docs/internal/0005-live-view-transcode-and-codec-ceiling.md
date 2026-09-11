# 0005 — Live view: go2rtc, mandatory transcode, and the codec/CPU ceiling

**Status:** accepted · **Date:** 2026-07-28 (consumer-driven encoding revised 2026-09-10)

## Context

The target cameras emit **HEVC (H.265) with no PTS** (presentation timestamps). Browsers can't
decode HEVC in a `<video>` tag, and go2rtc's fMP4 for these feeds carries a wrong track header plus
jittering sample durations (the cameras time nothing), which makes players **stall/freeze**. Their
ONVIF `SetVideoEncoderConfiguration` is a decoy — it 200s and changes nothing — so the codec can't be
fixed at the source.

## Decision

- **[go2rtc](https://github.com/AlexxIT/go2rtc)** is the media hub: it pulls each camera's RTSP,
  absorbs the transport quirks, and re-exposes clean streams. Its config is generated from the
  registry and reloaded on change.
- **Re-encode to H.264 is mandatory** for the browser. The per-camera selector defaults to the
  **main feed** (`_hd`, full resolution), because maximum camera quality is the product default.
  Users on weaker hosts may explicitly choose Auto (host-budget policy) or a locally downscaled
  640px stream (`_web`). Both originate from the same server-side producer.
- The encoder is pinned to a **fixed frame rate** (`live_fps`) with an `fps` filter because the
  cameras send no PTS — passing their timing through reproduces jitter and stalls. Output `-r`
  is deliberately not used: during an HEVC decoder failure it can keep assigning new timestamps
  to the last picture and make every liveness counter lie. An explicit target bitrate per quality
  level (`live_quality`, `media/quality.py`) is the picture lever. The FFmpeg input also uses
  go2rtc's `#async` mode (server wall-clock timestamps): one real unit advanced its nominal 10 fps
  timeline at ~3× wall speed without this, producing 13.1 Mbps and >100% CPU despite a 4.5 Mbps cap.
- **One camera RTSP session is the invariant.** Recording and both live variants share the base
  main-feed producer. `_hd` and `_web` are independent on-demand FFmpeg sources; neither is
  preloaded, and SD reads the base directly, not the HD transcode. Both retain `#async` and audio
  clock repair. go2rtc stops a variant after its last consumer disconnects. Clients requesting the
  same quality share one encoder; two qualities coexist only when both have real consumers
  (apart from asynchronous connection teardown during a switch). No `/onvif2` connection is added.
- The generated config overrides go2rtc's software H.264 template. This makes the configured
  two-second GOP effective (`-g:v live_fps*2`, fixed keyint, scene-cut disabled); previously the
  built-in template appended `-g 50` after our raw `-g 20`, and the last option silently won.
- The dashboard player prefers **WebRTC** (`mode=webrtc,mse`, MSE fallback). MSE (WebSocket/TCP)
  rebuffers on any jitter/loss over the internet; WebRTC's UDP + jitter buffer rides over it — this
  is what fixed remote HD "freezing to load".

## Consequences

- The browser no longer needs an HEVC decoder; live view works everywhere with low latency.
- Encoder cost is per consumed quality per camera, not per tab. With three recorded cameras and
  one live quality each, expect three recording FFmpeg processes plus three live encoders, not
  extra HD encoders behind SD. With no viewers, only recording FFmpeg processes remain.
  `grid_hd_max_cameras` remains the Auto-mode host guard; `live_hwaccel` can reduce encoding cost.
- Freeze recovery is end-to-end: the dashboard correlates go2rtc producer packet progress,
  WebRTC decoder frames and `requestVideoFrameCallback().mediaTime`. A client-only stall rebuilds
  only its PeerConnection. A confirmed producer stall first disposes that player, then waits for
  relay teardown through `POST /api/media/recover/{camera_id}`. Recovery removes legacy preloads
  but never adds one. Another viewer intentionally keeps its shared encoder alive: recovery does
  not forcibly disconnect other clients. The base producer/recording remain untouched. MSE uses a
  bounded queue and reconnects on overflow or `SourceBuffer` failure.
- A cold encoder may wait for the next camera keyframe when the first viewer connects or switches
  to an unused quality. This is the explicit tradeoff for not spending CPU/RAM on discarded video.
  Startup retains a 45-second grace period; steady-state freeze detection remains unchanged.
- **Rejected:** handing the browser HEVC directly (broken here, per Context) and per-camera
  `SetVideoEncoderConfiguration` to force H.264 at source (a decoy on these units).

## Validation — 2026-09-10

- 85 targeted tests passed (config/recording, freeze recovery, WebSocket teardown, quality),
  plus Ruff and mypy (151 source files).
- Bounded image build (512 MiB, one CPU), deployed build `b-617e4e1e2c5b`. Media container was
  stopped before applying the new configuration to avoid orphan encoders from an in-process
  reload; this one-time deployment interrupted streams/recording briefly.
- Three registered cameras online and recording after deployment. With the dashboard consuming
  one HD and two SD variants, six FFmpeg processes remained (three recorders + three live),
  compared with eight before. Live preload registry empty.
- Bounded MSE test on camera 3: an otherwise-unused HD producer started for the first client;
  two simultaneous HD clients shared its producer ID; closing one preserved the other; closing
  the last stopped the producer within five seconds. The base producer ID stayed unchanged
  throughout the test. No camera settings, lights, sirens or talkback were used.
- This validates resource lifecycle, not long-duration freeze immunity. Monitor cold-start
  latency and repeated manual/Auto switches in actual browsers; no browser was launched here.
