# ARM64 Android/Termux checkpoint

## Stable-server handover — 2026-10-28

### Live-view load incident

The owner reported repeated camera reloads on both LAN and HTTPS. A read-only
process snapshot found six FFmpeg processes, not a growing set of proven orphan
processes: three original-codec recorders retained their PIDs at roughly10–13%
CPU/37–40MiB RSS, while three HD view encoders each used113–128% CPU,46–51MiB
RSS and60–68 threads. MemAvailable was about853MiB. Later encoder PIDs changed
while recorder PIDs remained stable. This supports investigating live transcode
pressure/recovery, not claiming recording or tunnel failure; CPU percentages are
per-core lifetime samples, not utilization peaks or a complete causal proof.

The collected command options had no explicit decoder/filter/encoder thread caps.
`LIVE_FFMPEG_THREADS` now bounds input decoding and both filter schedulers before
the RTSP input, plus output software video encoding. Default2, accepted1–16; phone
deployment should start with1. Image resolution, bitrate and the explicit HD default
are unchanged. The95-test focused group passed, including a bounded real local
FFmpeg template check (118.1MiB suite peak under192MiB/swap0/CPU50%). This is not
three-HD phone acceptance. Deployment and real before/after results must be recorded
separately; the phone has not yet been claimed fixed by these synthetic checks.

Follow-up: the owner supplied the dedicated connector token privately. Native
cloudflared now runs enabled under Termux runit, with four active connections.
The configured public-hostname origin is `http://127.0.0.1:3200` on the phone.
The missing DNS record initially returned authoritative NXDOMAIN; after the owner's
correction, public Cloudflare DNS resolves the hostname. A TLS-verified tagged
HTTPS health probe using that DNS answer returned200 and did not appear in WSL
nginx logs; the phone connector has now processed167 requests. It reported two
request errors over its lifetime at that sample, not a zero-error/endurance claim.
No app/media restart or camera action was required. The old CCG nginx configuration
is retained as a rollback artifact, but the verified request no longer uses it.
The workstation resolver still had negative caching during this check; its global
DNS settings were not changed. The initial handover notes below describe the earlier
temporary relay, not the current verified public route.

Owner changed the operating topology: stable server on the phone, development on
WSL. A separate `/opt/ccg/releases/4650a29ffeacecc60455ca0fcecbf2a628b01996`
source release uses `/opt/ccg/current` and private `/opt/ccg/shared` state. The
original `/opt/ccg-arm-test-20261013` installation remains untouched. This source
revision passed CI5209 tests/1skip and its image workflow; Docker is not used on
the phone. Python3.14.4 installed binary ARM64 wheels; go2rtc1.9.14 retained its
previously verified SHA256. The new venv passed `pip check`.

An online SQLite snapshot and effective configuration travelled over authenticated
SSH without displaying secrets. Registry, encrypted vendor accounts/enrollments,
temporary access keys and runtime settings were retained. The primary/signing keys
were retained so encrypted credentials remain usable. A private pre-migration DB
snapshot is kept on the phone. Historical video remains on WSL; only its rows were
removed from the **new phone copy**, because those files were not transferred.
No original DB, video or other project container was deleted.

First acceptance used API-only startup, then stopped `ccg-app` and `ccg-go2rtc`
on WSL before enabling phone media. Those containers remain stopped. Phone process
checks found one go2rtc and three recorders; a reduced viewer temporarily adds its
encoder. There is a handover recording gap; this is not seamless migration.

Measured HTTPS smoke through `ls-ccg.bytec.cloud`:

- Health200; unauthenticated cameras401; `/.env`404; primary login with Secure cookie.
- New three-minute live-only test access logged in, could not manage accesses403,
  and lost access immediately on revocation (`/me` unauthenticated, cameras401).
  The probe's key was revoked in cleanup, not left active.
- Camera3 reduced MSE:3048656bytes/676binary messages over a30s receive window;
  first binary data4.796s. Encoded delivery, not visual browser-smoothness acceptance.
- Camera3 recording indexed and original HTTP Range returned206/256bytes.
- Local probe capped192MiB/swap0/CPU50%; observed46.4MiB peak. No WSL build,
  browser, emulator, sound, light or movement command.

Termux runit supervises the app, with bounded rotating logs. TERM to PRoot alone
did **not** stop the initial API-only app. The deployed `control/t` hook now validates
the actual app PID/command and sends TERM to Uvicorn; a later media stop completed
in about1s with no FFmpeg/go2rtc remaining. Preserve PRoot kill-on-exit. The app was
then restarted/enabled. This does not establish survival of Android suspension or
automatic startup after device reboot.

Cloudflared2026.10.5 was installed natively in Termux, not as a container. Its own
service is configured **down** until a dedicated private token is supplied. The
workstation's existing tunnel also serves unrelated projects and was not replicated.
For continuity, only the CCG nginx upstream was changed to the phone's LAN endpoint;
other applications and the shared tunnel were not restarted. Public HTTPS currently
still depends on that workstation relay. A backup of the original CCG proxy config
is in the ignored deployment workspace. Final step: create a phone-only tunnel,
point this hostname to phone loopback3200, verify it, then retire only the old CCG
hostname route. Do not stop the shared workstation tunnel.

The new [workflow](../public/termux.md#stable-phone-development-workstation) and
`scripts/package_release.py` distinguish committed source releases from OCI images.
Uncommitted native media/PTZ factory work in WSL was intentionally excluded from
the deployed revision. ARM control behavior, continuous load/thermal/background
acceptance and native Linux Docker remain separate validations.

## Original bounded hardware test — 2026-10-13

2026-10-20 review: the owner requested public installation instructions for this
non-Docker path. The bounded results below support an experimental guide, not a
claim of three-HD or24/7 reliability. See [Termux setup](../public/termux.md).
This review reused the recorded evidence; no new phone installation or media run
was performed. Native Linux Compose validation is independent and deferred.

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
