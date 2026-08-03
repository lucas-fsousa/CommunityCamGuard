# 0005 — Live view: go2rtc, mandatory transcode, and the codec/CPU ceiling

**Status:** accepted · **Date:** 2026-07-28 (WebRTC-first refined 2026-08-02)

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
- **Re-encode to H.264 is mandatory** for the browser. To keep it affordable it runs on the
  **substream** for the grid (`_web`, cheap) and on the **main feed** for single-camera view
  (`_hd`, full resolution); go2rtc keeps an idle `ffmpeg:` source free until something watches it.
- The encoder is pinned to a **fixed frame rate** (`live_fps`) because the cameras send no PTS —
  passing their timing through reproduces the jitter and stalls. An explicit target bitrate per
  quality level (`live_quality`, `media/quality.py`) is the picture lever.
- The dashboard player is **WebRTC-first** (`mode=webrtc,mse`, MSE fallback). MSE (WebSocket/TCP)
  rebuffers on any jitter/loss over the internet; WebRTC's UDP + jitter buffer rides over it — this
  is what fixed remote HD "freezing to load".

## Consequences

- The browser no longer needs an HEVC decoder; live view works everywhere with low latency.
- **Two hard ceilings, both external to the pipeline:** the grid's picture ceiling is the camera's
  substream (often ~640×360 / tens of kbps — no encoder setting recovers detail never sent); the CPU
  ceiling is the host (a 1080p transcode is ~30% of a core per viewer). `grid_hd_max_cameras` is the
  host-budget guard; hardware transcoding (`live_hwaccel`) is the lever to raise it.
- **Rejected:** handing the browser HEVC directly (broken here, per Context) and per-camera
  `SetVideoEncoderConfiguration` to force H.264 at source (a decoy on these units).
