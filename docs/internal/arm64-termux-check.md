# ARM64 Android/Termux checkpoint

## Deployment ended: owner returned to WSL

On2026-10-28 the owner ended the Termux production experiment and requested a direct
Docker rebuild on WSL, without further database migration, and removal of the CCG
installation on the phone. The validatedd15bf32 source snapshot was built with
512MiB/no extra swap/50% CPU per build step; source-only research changes in the
working tree were excluded. The rebuilt app and go2rtc run on WSL again, build
`b-5dd00c822bcd`, using the original database and recordings. Authentication, three
recorders, media health and public HTTPS were checked.

The old workstation tunnel's current remote route points the public hostname to
`http://localhost:3200`; the phone-only tunnel now has only a404 fallback rule.
The temporary extra WSL connector was therefore removed, while the original shared
workstation tunnel and other applications remained running. No DNS change was needed.

Termux `ccg` and `ccg-cloudflared` services were gracefully stopped and disabled
before WSL captured the cameras. When attempting the authorized filesystem cleanup,
SSH at the known phone endpoint refused connections. The delete command did not
execute; the `/opt/ccg` and old test installation must not be reported removed.
Previously copied private state and approximately1.1GiB of footage remain in the
ignored WSL handover workspace, but were not merged into production. This is not a
complete backup of the final phone segments. Reopen SSH to finish removal of only
CCG app/service/token artifacts, preserving SSH and the rest of Termux/Ubuntu.

The measurements below remain experimental hardware evidence, not a recommendation
or claim that three simultaneous HD views are accepted on that phone.

## Stable-server handover — 2026-10-28

### Live-view load incident

#### Follow-up: investigation while the owner retained HD

The owner reported that delay improved without selecting SD and requested further
read-only investigation. No quality, service, governor or clock setting was changed.
A fresh8.23s process sample found all relevant processes permitted on CPUs0–7 in
the Android `top-app` cpuset, without per-process swap. CPU/address-space rlimits
were unlimited; cgroup CPU-quota files were not readable, so quota absence is not
claimed. The PRoot tracer consumed67.9% of one core, app48.1%, go2rtc24.4% and
the three live encoders142.1/132.8/117.0% (per-core notation).

Additional limits were observable: policy4's current and allowed maximum clocks
were1.4016GHz versus a2.016GHz hardware maximum; policy0 was capped at1.4208GHz
versus1.8048GHz. The same caps remained on recheck. CPU thermal sensors read about
66–70°C, with `thermal-cpufreq-4` cooling state3/8 and `thermal-cpufreq-0`2/8.
These are active cooling states, not merely an idle low-frequency reading. See
[kernel thermal semantics](https://kernel.org/doc/html/v5.7/driver-api/thermal/sysfs-api.html).
Do not disable thermal safety controls or force higher clocks as a software fix.

An aligned LAN comparison of the existing camera3 base HEVC and HD H264 restreams
still found approximately32.18s extra delay in the HD path: camera OSD01:10:39 vs
01:10:06, first video-fragment arrival timestamps1793207439.233537 and
1793207438.4101353 respectively. Subtracting their0.8234s arrival difference avoids
confounding keyframe startup waits. OSD resolution gives about±1s precision; the
camera's timezone offset cancels. This does not isolate decoding, encoding and
interleaving queues separately. No additional camera connection or encoder was opened.

One further software candidate is WebSocket compression. A handshake with the
authenticated app negotiated `PerMessageDeflate` without requesting any media.
The launch code does not override Uvicorn's
[enabled-by-default setting](https://github.com/encode/uvicorn/blob/master/docs/settings.md).
The pinned go2rtc upstream does not enable that compression, so this is not a claim
of two compressed hops. An offline whole-file proxy test on8,747,222bytes of the
captured HD MP4 produced8,767,144bytes with deflate: no bandwidth saving in that
sample. It is not a live per-message or ARM CPU benchmark, and does not quantify
the benefit of disabling it. Test that candidate while preserving HD/authentication,
then compare thread budgets1/2 under a controlled load; neither change was applied.

The Ubuntu FFmpeg lists compiled VDPAU/CUDA/VAAPI/DRM/OpenCL/Vulkan methods, not
MediaCodec. No native Termux FFmpeg or DRM render node was observed. Compiled
method names do not prove working acceleration. Evaluating a native Android worker
is separate work; no package was installed or hardware capability advertised here.

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

The fix was subsequently deployed as source release
`7435b6586f0898a55347a59f3ac206aaff233415`, build`b-5dd00c822bcd`, with
`LIVE_FFMPEG_THREADS=1`. CI40728866238 passed5248 tests/2skip, Ruff/Mypy and all
Node groups. Image40728449110 passed for identical runtime code4b69465; the
follow-up commit only classified the setting as server-only. The first CI failure
was that missing classification and was corrected before activation, not ignored.

The staged venv copied the existing validated binary dependencies (unchanged
pyproject), without reinstalling or modifying the old venv. SHA256 was verified,
then imports/sys.prefix checked from the candidate release. A graceful app stop
preceded activation; port3200 and old FFmpeg/go2rtc closure were verified before
switching. Private database/config snapshots and release4650a29 remain available.
Only the app/media service restarted; cloudflared and camera configuration stayed
untouched. A handover recording gap is possible/expected.

With three HD encoders active again, each had11 threads instead of60–68. Their
PIDs27891/27936/27937 remained unchanged across the subsequent samples. One camera3
HD consumer shared the existing encoder and received9088858bytes/413binary messages
over a20s receive window, first binary data0.342s. WebSocket teardown extended the
probe to31.353s; these are encoded bytes, not human visual acceptance. The local
probe used44.4MiB under192MiB/swap0/CPU50%. No speaker/light/movement action.

CPU remains high: a later5.27s sample measured121.4/118.0/131.5% per-core CPU
for the HD encoders, with approximately1.18GiB MemAvailable. This was not a controlled
comparison and does **not** establish adequate capacity for three HD feeds. LAN
health returned200 in0.45s in a separate sample. No browser diagnostic events were
received after restart, so absence of those events does not prove stall-free video.
The owner was asked to refresh and validate the same three-camera view. SD/Auto or
a single HD view remains the fallback performance choice; resolution/bitrate and
the user's explicit HD preferences were not silently lowered.

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
