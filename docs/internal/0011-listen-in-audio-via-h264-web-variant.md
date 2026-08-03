# 0011 — Listen-in audio via the H.264 `_web` variant

**Status:** accepted · **Date:** 2026-07-27

## Context

The cameras are **HEVC video + PCMA/G.711 audio**, and the live dashboard was silent — a codec
pincer:

- **WebRTC** carries G.711/Opus audio fine but **cannot do HEVC** video.
- go2rtc's **MSE** path can show HEVC but **won't reliably mux audio** alongside it, so the browser
  gets a video-only track and disables the volume control.
- A native `<video>` on an HEVC stream can't decode it in Chrome at all.

So there was no single path that gave both HEVC video *and* audio in the browser.

## Decision

Play a **browser-friendly H.264 variant** in go2rtc's own low-latency player. For cameras with an
audio track, `build_config` adds `cam_<mac>_web = ffmpeg:cam_<mac>#video=h264#audio=aac#audio=opus`.
Transcoding video to H.264 (plus AAC for MSE and Opus for WebRTC) lets the player negotiate
**WebRTC** — H.264 video and audio together, low latency, working volume (unmute to listen). Audio
is gated on the capability probe (`has_audio`); cameras without audio play the base stream.

The **base stream is untouched**: the recorder keeps `-c:v copy` on the original HEVC. go2rtc runs
the `_web` transcode only **while the stream is being viewed**, and resolves `ffmpeg:<stream>`
against the single base camera connection.

## Consequences

- Listen-in works with near-real-time latency (a native `<video>` on an MP4 stream was tried and
  rejected — it works but adds seconds of latency).
- **Trade-off:** the H.264 transcode costs CPU per *viewed* audio camera (HEVC passthrough would be
  free but silent). Acceptable for a handful of cameras while watching; the same `_web`/`_hd`
  transcode design and its CPU ceiling are covered in ADR 0005.
- Two-way audio ("talk"/back-channel) is a separate effort — it isn't reachable over ONVIF here
  (see ADR 0008).
