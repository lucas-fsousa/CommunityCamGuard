# 0005 — Live view: go2rtc, mandatory transcode, and the codec/CPU ceiling

**Status:** accepted · **Date:** 2026-07-28 (freeze recovery refined 2026-08-10)

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
- **One camera RTSP session is the invariant.** The base main-feed producer is shared by recording
  and one preloaded `_hd` FFmpeg transcode. Browsers attach only to this already-running local H.264
  producer; opening tabs or reconnecting WebRTC does not touch the camera. `_web` is downscaled
  locally from `_hd`, so even selecting SD never opens the camera's `/onvif2` feed. Runtime
  validation showed exactly two established port-554 connections for two cameras.
- The generated config overrides go2rtc's software H.264 template. This makes the configured
  two-second GOP effective (`-g:v live_fps*2`, fixed keyint, scene-cut disabled); previously the
  built-in template appended `-g 50` after our raw `-g 20`, and the last option silently won.
- The dashboard player prefers **WebRTC** (`mode=webrtc,mse`, MSE fallback). MSE (WebSocket/TCP)
  rebuffers on any jitter/loss over the internet; WebRTC's UDP + jitter buffer rides over it — this
  is what fixed remote HD "freezing to load".

## Consequences

- The browser no longer needs an HEVC decoder; live view works everywhere with low latency.
- The CPU ceiling is now per camera, not per viewer: one 1080p H.264 transcode stays hot at roughly
  30% of one core and all HD consumers share it. A requested SD variant adds a local downscale but
  adds no camera load. `grid_hd_max_cameras` remains the Auto-mode host guard; hardware transcoding
  (`live_hwaccel`) is the lever to raise it.
- Freeze recovery is end-to-end: the dashboard correlates go2rtc producer packet progress,
  WebRTC decoder frames and `requestVideoFrameCallback().mediaTime`. A client-only stall rebuilds
  only its PeerConnection. A confirmed producer stall first disposes that player, then cycles the
  `_hd` preload through `POST /api/media/recover/{mac}`. This restarts only the local decoder/
  encoder; the base RTSP producer and recording continue uninterrupted. The MSE fallback uses a
  bounded queue and reconnects on overflow or `SourceBuffer` failure.
- The preloaded producer absorbs the camera's slow first IDR once at service startup. Browser
  reloads then attach to a hot H.264 source and should not repeat that wait. Startup still gets a
  45-second grace period; once started, the normal ten-second freeze threshold applies.
- **Rejected:** handing the browser HEVC directly (broken here, per Context) and per-camera
  `SetVideoEncoderConfiguration` to force H.264 at source (a decoy on these units).
