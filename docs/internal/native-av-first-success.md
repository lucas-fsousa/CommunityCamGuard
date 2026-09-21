# First successful native AV reception — camera 3, 2026-09-21

## Result

One authenticated, empty loopback POST to the reserved diagnostic returned
**HTTP 200 after 6.23 seconds**. It ran in the server process with exclusive
camera ownership. The exact camera-3 MAC/registry/enrollment association was
verified before activation; neither installed camera was selected.

```json
{
  "media": {
    "ready": true,
    "close_acknowledged": true,
    "datagrams": 91,
    "received_bytes": 29238,
    "sent_bytes": 2506,
    "headers": 1,
    "video_frames": 29,
    "audio_frames": 45,
    "ignored_datagrams": 20,
    "peak_buffered_bytes": 1992,
    "meter_acknowledgements": 2
  },
  "route_release_acknowledged": true
}
```

Safe log: `stage=route_release outcome=av_completed error_type=none
release_attempted=True release_acknowledged=True elapsed_ms=6224`.

This confirms fresh authenticated route preparation, correlated meter roundtrip,
AV negotiation/header readiness, parsed native video/audio reception, AV CLOSE
transport receipt and B9 route-release receipt. No Android/emulator gateway was
used. The SDK-backed reply-extension correction unblocked the prior metering
failure without removing peer/route/sequence/timestamp checks.

This does **not** prove decoded live pictures, audible playback, keyframe readiness,
resolution, frame rate, synchronized A/V, long-session reliability, physical
resource teardown or internet-independent provisioning/authentication. The counts
are parsed protocol records, not decoder output. Payloads were discarded rather
than saved. `peak_buffered_bytes` is the protocol counter, not total process RAM.
Ignored packets were counted, not individually diagnosed.

## Deployment and restoration

- Commit `afa5f04` had terminal CI success, run `35668905016`, before build.
- Image `c9c4b6c05849` built with a cached builder capped at 512 MiB, no extra
  swap and one CPU; build context 3.894 MB. No browser, emulator or decoder ran.
- Only the app was recreated for activation and restoration. This briefly
  interrupted recorder ownership; no zero-downtime recording claim is made.
  go2rtc and unrelated WSL projects retained their uptime.
- Ignored Compose override was reset to disabled/empty before the single POST.
  Runtime restoration followed result/log collection in the same command sequence.
- Runtime verification confirmed disabled diagnostic, cleared target and health
  HTTP 200. Base RTSP producers remained at one per camera with advancing ingress
  bytes after restoration. This is not a browser smoothness check.
- No audio transmission, movement, siren, light, reboot or repeated attempt was
  requested. Received audio counts refer to inbound media only.

## Next implementation checkpoint

Design a separately gated, short camera-3 sample that feeds the already offline-
validated elementary-stream/decoder path under strict duration, byte and process
memory limits. Keep bounded backpressure and teardown on every exit; do not start
a persistent stream or add another production camera connection merely to test it.
Choose transient consumption or explicit owner-only ignored storage with a cleanup
policy; live video/audio are sensitive data and must not enter logs or Git.

Prove a real decoded keyframe and coherent timestamps before considering a generic
driver-native media adapter with RTSP fallback. Single source/local fan-out,
driver-owned capabilities and no automatic support grant remain invariants.
Dashboard integration and sustained reliability remain separate milestones.

Prior evidence: [SDK/bootstrap investigation](native-av-sdk-bootstrap.md).
