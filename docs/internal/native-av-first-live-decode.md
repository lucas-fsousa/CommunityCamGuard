# First successful live native-video decode — camera 3, 2026-09-22

## Evidence

Commit `bd52dfc` passed CI `35757490941` before deployment. Image `fcc77b2f47ce`
was built with the cached builder capped at 512 MiB / one CPU (3.922 MB context).
Camera-3 MAC/registry/enrollment matched the private inventory before activation.
One authenticated, empty, direct-loopback POST with the server-side decode opt-in
returned **HTTP 200 after 6.72 seconds**:

```json
{
  "route": {
    "media": {
      "ready": true,
      "close_acknowledged": true,
      "datagrams": 109,
      "received_bytes": 34695,
      "sent_bytes": 3046,
      "headers": 1,
      "video_frames": 44,
      "audio_frames": 46,
      "ignored_datagrams": 20,
      "peak_buffered_bytes": 4980,
      "meter_acknowledgements": 2
    },
    "route_release_acknowledged": true
  },
  "video": {
    "frames": 44,
    "width": 640,
    "height": 360,
    "input_bytes": 7253,
    "timestamp_span_ticks": 2849000,
    "pre_idr_discarded": 0
  }
}
```

Route telemetry: `stage=route_release outcome=av_completed error_type=none
release_attempted=True release_acknowledged=True elapsed_ms=6436`.

The 44 native HEVC records formed one configured-IDR-started sample. FFprobe found
exactly 44 frames and dimensions matching the camera's encoding header; strict
FFmpeg decode succeeded. Both decoder children ran **after** AV CLOSE/B9 receipt
and local socket cleanup. Successful HTTP completion also implies sample clearing
in the diagnostic's `finally`. No media file/image was produced or retained.

This is actual new live-camera data, not historical PCAP replay. No vendor Android
app/emulator was used as a gateway. It does not establish cloud-independent
authentication: current route setup still uses its existing P2P access flow.

## Scope and restoration

- Exactly one invocation, no retry. No speaker audio, microphone, siren, lighting,
  motion or reboot commands. Inbound audio was counted but not retained/decoded.
- Only the app was recreated for activation/restoration; this briefly interrupted
  recorder ownership. go2rtc and unrelated WSL projects retained their uptime.
- The ignored override reset both diagnostic flags and cleared target values
  before invocation; runtime restoration followed safe result/log collection in
  the same shell sequence. Runtime checks confirmed both flags false, targets
  empty, health HTTP 200 and one producer per base RTSP camera with advancing
  ingress bytes in post-restoration observations.
- Peak buffered bytes is the protocol counter, not total process RAM. Existing
  app memory limits and decoder process limits remained in force.
- No production native-video capability or dashboard player change was enabled.

## What remains

1. Map/prove the native stream's **maximum-resolution profile**. This diagnostic
   negotiated 640×360; it must not silently become the dashboard's default quality.
   Preserve the requirement to prefer maximum camera resolution, with user-selected
   or explicitly configured performance reductions.
2. Design the generic driver-native source contract, single-source/local fan-out
   and RTSP fallback without adding a persistent second camera connection.
3. Validate sustained reception, bounded backpressure, decoder recovery, timestamps
   and A/V synchronization. The short sample has a raw relative tick span only;
   do not infer frame rate/clock scale or long-session health from it.
4. Integrate browser playback and perform visual/latency review. Null-output decode
   proves decodability, not visual quality, correct orientation or frontend fluency.

The recordings-player/settings/security backlog remains separate and unchanged.
Implementation and limits: [decode trigger](native-av-decode-trigger.md).
