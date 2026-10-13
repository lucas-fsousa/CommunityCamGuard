# ARM64 Android/Termux checkpoint

Date: 2026-10-13. Tested source: `52e4ba5`. Experimental, bounded hardware test,
not Linux ARM64 Docker homologation or a browser soak test.

## Environment and isolation

- Redmi Note 8, Android 11, aarch64, approximately 3.55 GiB RAM.
- Existing Termux Ubuntu 26.04.1 ARM64 under PRoot; Python 3.14.3,
  FFmpeg 8.0.1, official go2rtc 1.9.14 `linux/arm64` binary.
- Docker client was present, but no accessible running Docker daemon. This test used
  a Python virtual environment, not Compose. [PRoot-Distro](https://github.com/termux/proot-distro)
  supplies a rootless Linux userland, not a separate Linux kernel.
- Isolated installation: `/opt/ccg-arm-test-20261013`, owner-only directory and `.env`.
  Copied tracked backend/frontend and packaging files, not the original filesystem.
  Python dependencies installed as binary wheels, without native compilation.
- Copied the three camera registrations/RTSP credentials and primary dashboard key
  over authenticated SSH; camera credentials were encrypted in the new registry.
  Did not copy vendor enrollment/account tokens, temporary access keys, previous
  recordings, the original `.env`, RE material or Git history.
- Control-session warmup disabled; no PTZ, sound, light, provisioning or native
  account commands were tested. No change to driver capability policy.
- Original WSL service uses a different Python/FFmpeg stack. This is not an
  identical-container comparison. The official go2rtc version matches WSL.

Official [go2rtc release](https://github.com/AlexxIT/go2rtc/releases/tag/v1.9.14)
ARM64 binary SHA256 verified:
`359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50`.

## Procedure and evidence

API-only startup left WSL running and disabled media autostart. Health and primary
login returned 200, unauthenticated camera listing 401, and `/.env` 404. Authenticated
listing returned three cameras. Five LAN health requests took 35–57 ms; these are
not browser or video latency measurements.

For media, stopped the two WSL camera services and ran a foreground phone supervisor
with a 300-second limit. A separate eight-minute WSL restoration timer provided a
fallback. The stop operation was still completing when the phone supervisor was
launched; both WSL containers were confirmed stopped by the first phone health check.
This does not establish zero connection overlap during the transition.

Authenticated MSE WebSocket probes received actual encoded media. No browser,
emulator or unbounded load generator was started. Three original-resolution
recorders ran throughout the phone media window.

| Workload | Observed result |
| --- | --- |
| Three recordings | 15 MP4 files, five per camera, approximately 66.3 MiB total. Latest file from each: HEVC 1920×1080 + AAC, valid to ffprobe; final segments were partial on shutdown |
| One HD viewer | 14.6 MB / 628 binary messages in 30 seconds; first data at 6.5 seconds. Saved sample decoded 30 H.264 1080p video frames and 46 AAC frames successfully |
| Three HD viewers | Did not pass this bounded probe: two received only the 1,139-byte initialization message; the third received about 2 MB, had a 5.18-second inter-message gap and connection closure |
| Three reduced-resolution viewers | Each received about 3.1 MB / 709–715 messages, with first data in 3.1–4.7 seconds and maximum subsequent gaps of 0.47–1.14 seconds. All samples identified as H.264 640×360 + AAC |

Viewer probes targeted a 30-second receive window with a 10-second receive-idle
timeout; socket shutdown can extend wall time. Initialization messages count as
binary messages but are not proof of delivered video. Reduced viewing resolution
did not reduce the original recording resolution. No visual smoothness or absence
of frozen scenes was confirmed by a person/browser.

During three-HD load, sampled MemAvailable was approximately 997 MiB. App RSS was
about 82 MiB, go2rtc 45 MiB, recorders 37 MiB each and live encoders 45–72 MiB each.
Live FFmpeg lifetime CPU samples were 105–160% per process in per-core notation;
PRoot also consumed CPU. These are samples, not peaks or whole-device utilization.
CPU/PRoot overhead is a possible bottleneck, not an established root cause of the
three-HD failure. Android OOM logs and temperatures were not collected.

## End state and remaining validation

The bounded phone server stopped, with no remaining go2rtc/FFmpeg processes in the
final process check. WSL services were restarted, health returned OK, and all three
recording directories grew afterward. The fallback timer was cancelled. Transition
recording gaps are possible; this is not a gap-free handover claim.

The phone installation, private configuration (including the dashboard key) and
test recordings remain for reuse, but the phone dashboard is **not running**. No
boot service or Android power-management changes were installed. Treat that private
copy as credential-bearing even after rotating the temporary SSH password.

Next ARM work:

- Native Linux ARM64 Compose build/start on real hardware, without x86 emulation.
- Longer browser/media/recording soak with CPU, encoder progress, temperature and
  reconnect measurements; distinguish encoder throughput from keyframe/timeout effects.
- Validate Android background suspension and storage/power behavior before any
  always-on deployment claim. Repeat with a deliberate resource budget.
- Test discovery and model-supported controls/two-way audio separately; this media
  check does not homologate those features on ARM.
- Evaluate platform-specific hardware acceleration separately from software encoding.

Until then, three reduced-resolution viewers with original recordings are a more
promising configuration for this particular phone than three software-encoded HD
viewers. No universal ARM performance conclusion is warranted.
