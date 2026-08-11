# Decisions & Context Log

The chronological engineering journal for **Community Cam Guard** — the full narrative behind each
change. The distilled, load-bearing architecture decisions are being migrated into focused **ADRs**
under [`internal/`](internal/); those are authoritative for what they cover. This log remains the
fuller record and the source for anything not yet migrated.

> Examples here use de-identified placeholders (`aa:bb:cc:dd:ee:ff`, `192.168.1.x`) — never real
> device data.

---

## 1. What we're building

An open-source dashboard to **discover, stream, record and control generic Chinese ONVIF/RTSP
Wi-Fi cameras**, replacing the limited/risky vendor app. Runs on WSL (Windows 11 host).
Priority: support **as many generic cameras as possible** by probing each camera and enabling
only the features it actually supports.

### Functional requirements (from the user)
- Web dashboard: view cameras individually, as a full surveillance grid, or a custom panel of
  user-selected cameras.
- Automatic camera discovery on the network.
- **24/7 recording** to disk (user-configurable location), stored **efficiently in chunks** so
  large files are easy to copy and a corruption never loses the whole recording. This is one of
  the most important requirements.
- Storage alert at **80%**. The app **never deletes anything** — the user decides what to
  remove. When storage is full: keep the alert on screen, **skip saving, keep streaming**, and
  resume saving automatically once space frees up.
- **Secret key** from `.env` gates the dashboard (any value, any length) via a signed session
  cookie — safe even if exposed to the web.

### Non-functional / per-camera capabilities (enable only if supported)
- PTZ (pan/tilt/zoom rotation).
- Speaker audio out.
- Microphone / two-way audio.
- LEDs (blink) and "anti-thief" siren/floodlight mode.

### Stack (given + decided)
- Backend: **Python / FastAPI**.
- Media engine: **go2rtc** (RTSP→WebRTC low latency + two-way audio; single static binary).
- Storage: **local-first**, **S3 optional** via `.env`.
- Frontend: lightweight/low-cost, easily hostable (planned: static Vite build). Not started.
- License: **MIT**, open-source.

---

## 2. Decisions made and WHY

| Topic | Decision | Why |
|---|---|---|
| WSL networking | Require `networkingMode=mirrored` (Win 11) | Default NAT can't reach LAN cameras; multicast ONVIF discovery doesn't cross NAT. Mirrored shares host interfaces. |
| Discovery lib | **Stdlib only** (raw UDP multicast SOAP), no onvif/zeep | ONVIF/zeep wheels are flaky on Python 3.14 (host runs 3.14); keeps "works everywhere". |
| Media engine | go2rtc (not pure-Python ffmpeg/HLS) | Low-latency WebRTC, two-way audio (mic), handles many camera quirks; single binary. User chose it. |
| Recording | ffmpeg **segment muxer, `-c copy`**, 60s chunks | Remux instead of re-encode → near-zero CPU. Small chunks = easy copy/delete, corrupt file costs 1 min. |
| Recording layout | `recordings/<camera_id>/<YYYY-MM-DD>/<HH>/<timestamp>.mp4` + **SQLite** index | Easy to find/delete by day; index powers the playback timeline. |
| Storage backend | Local-first, optional S3 **tiering** | 24/7 direct-to-S3 is expensive (bandwidth + PUT + storage). User chose local-first. |
| Full-storage policy | Alert at 80%; when full skip-save, keep stream, auto-resume; **never auto-delete** | Explicit user requirement. |
| Auth | Secret key in `.env` → signed session cookie (itsdangerous) | Simple, safe to expose; any key length. |
| Feature gating | Probe ONVIF capabilities per camera; UI enables only supported features | Cameras are generic/varied; avoid dead buttons. |
| **Camera identity** | **Key each camera by its MAC address, not IP** | IP is a DHCP lease that changes; MAC is fixed in hardware. Under WSL `mirrored` we share the LAN L2, so the MAC is readable from `/proc/net/arp` after connecting. Confirmed live 2026-07-27. IP is stored as a mutable "last-seen address" refreshed each scan by matching MAC. |
| Default login | Pre-fill username **`admin`** in the add-camera UI | These generic cams ship with `admin`; user just fills the password. |
| Credential flow | Discovery lists candidates → user adds login/password + friendly name → probe confirms → store per-MAC | User wants the dashboard to surface cameras and let them attach creds, keyed to a stable identity. |

---

## 3. WSL mirrored networking — setup & the user's concern

**User's worry:** they run other WSL projects using **cloudflared tunnels + nginx** and fear
mirrored will break them.

**Assessment (reassuring):**
- cloudflared = outbound tunnel to Cloudflare edge, reaches local services via `localhost` →
  keeps working under mirrored.
- nginx reached via `localhost` → keeps working (mirrored preserves/improves localhost both ways).
- Only breaks setups that **hard-code the old `172.x` WSL IP** or use custom `iptables`/routing.
- Bonus: multicast (needed for ONVIF discovery) starts working under mirrored.
- **Fully reversible.**

**Enable** — edit `C:\Users\<you>\.wslconfig` on the Windows host:
```ini
[wsl2]
networkingMode=mirrored
```
Then in PowerShell: `wsl --shutdown`, reopen WSL.

**Rollback:** remove the line (or set `networkingMode=nat`) + `wsl --shutdown`.

**Symptom of mirrored being OFF:** discovery returns empty, and `_local_ipv4()` reports a
`172.x` address (the NAT interface). Under mirrored it reports the host's LAN IP.

---

## 4. Discovery strategy (layered, most-reliable first)

1. **ONVIF WS-Discovery** (multicast 239.255.255.250:3702) → find devices + their ONVIF
   service URL (XAddrs) + scopes (name/hardware). **[DONE]**
2. **ONVIF `GetStreamUri`** → the real RTSP URL (better than guessing paths). **[NEXT]**
3. **ONVIF `GetCapabilities` / `GetServices`** → detect PTZ, audio, imaging, digital I/O
   (LED/siren). Drives per-camera UI gating. **[NEXT]**
4. **Fallback active scan** when ONVIF absent: scan subnet on common ports (554 RTSP, 80/8000
   HTTP, 8899, 34567/37777 proprietary) and try common RTSP paths. **[DONE]** —
   `discovery/active_scan.py`, stdlib-only, threaded; confirms RTSP via OPTIONS then probes
   paths with DESCRIBE (Basic/Digest auth). Live run found 2 cameras (see §5).

Cameras need credentials (user/pass) for ONVIF SOAP + RTSP — stored per-camera (to be
encrypted) in config. User confirmed their cameras **do** expose RTSP/ONVIF on the LAN.

RTSP stream paths to try live in **camera profiles**, `discovery/profiles.py` (was
`endpoints.py` — see the rename note at the end), ordered most-common-first so the gentle
probe hits paydirt early. Seeded from field testing (Yoosee =
`/onvif1` main + `/onvif2` sub) and the **iSpyConnect community DB**
(https://www.ispyconnect.com/camera/yoosee — good source for per-model brand→endpoint mappings;
grow the whitelist from here). Supports `[USERNAME]/[PASSWORD]/[CHANNEL]` placeholder templates
(e.g. XiongMai `/user=..&password=..&channel=1&stream=0.sdp?`), emitted only when creds exist.
Lesson: we initially missed `/onvif1//onvif2` because the path list was ad-hoc guessing — a
curated brand whitelist is the right foundation for discovery.

---

## 5. Current status

**Done:**
- Project scaffold: `pyproject.toml`, `.env.example`, `.gitignore`, `README.md`.
- `backend/app/config.py` — Settings from `.env` (pydantic-settings).
- `backend/app/discovery/ws_discovery.py` — WS-Discovery, stdlib only. **Parsing tested OK**.
  Live run (2026-07-27, mirrored ON) returned **empty** — the cameras don't answer multicast.
- `backend/app/discovery/active_scan.py` — active subnet scan fallback (stdlib, threaded).
  Confirms RTSP via OPTIONS, probes common paths via DESCRIBE (Basic/Digest), and captures
  the **MAC from `/proc/net/arp`** as the stable identity.

**Cameras found (live 2026-07-27), both Dahua-style, stream path
`/cam/realmonitor?channel=1&subtype=0` (subtype=1 = sub-stream):**

| MAC (identity) | last-seen IP | port | notes |
|---|---|---|---|
| `aa:bb:cc:dd:ee:01` | 192.168.1.101 | 554 | DESCRIBE 200 on realmonitor path |
| `aa:bb:cc:dd:ee:02` | 192.168.1.102 | 554 | DESCRIBE 200 on realmonitor path |

Test creds provided: `admin` / `<redacted-pw>` or `<redacted-pw>` (one per camera).
Known limitation: the RTSP Digest builder returns 400 against a genuine Hikvision-style
`/Streaming/Channels/101` challenge — revisit if a real Hikvision camera appears (not needed
for the user's Dahua-style cams).

**Registry (DONE):** `backend/app/db/registry.py` — SQLite keyed by MAC, passwords encrypted
with Fernet (key = sha256 of `.env` secret → urlsafe-b64). `Camera` dataclass + CRUD +
`rtsp_url` builder + `reconcile(scan)` → (configured cams w/ refreshed IP, new candidates).
Tested end-to-end 2026-07-27: both cams registered, at-rest encryption verified (plaintext
absent from blob), reconcile matched by MAC and refreshed IPs. Added `cryptography>=42`
(installs clean on 3.14 via abi3 wheel) and `db_path` setting (`./data/ccg.db`).

**Streaming quirk found (2026-07-27):** the cams' SDP is **H.265**, RtspServer_0.0.0.2
(cheap HiSilicon/XiongMai stack), `a=control:track1`, no `Content-Base`. OPTIONS+DESCRIBE
return 200 **anonymously**, but every `SETUP` returns **400 Bad Request** — direct ffmpeg/
ffprobe cannot pull the stream (non-compliant control-URL handling). This is exactly what
**go2rtc** is meant to absorb; plan: go2rtc connects to the camera and restreams a clean
local endpoint that ffmpeg records via `-c copy`.

**go2rtc installed** (`bin/go2rtc` v1.9.14) and tested — it ALSO fails: `wrong response on
SETUP`. Deep RTSP investigation (2026-07-27): path `/cam/realmonitor?channel=1&subtype=0` is
confirmed (DESCRIBE 200 across channel/subtype; other paths 401/400), password `<redacted-pw>`
valid. But **every SETUP returns 400** — TCP-interleaved AND UDP, anonymous AND Basic/Digest,
every control-URL construction, from python + ffmpeg + go2rtc, and still 400 after a 90s rest
(so not session exhaustion). The 400 comes straight from the camera (carries its `Allow:`
header), so it is NOT a UDP-inbound/firewall media issue. SDP is quirky (rtpmap H265 but fmtp
carries H264 SPS/PPS). **User reports the same cameras streamed fine via ffmpeg on Windows,
failing only on WSL under NAT.** **RESOLVED (root cause = camera state, not path/net) 2026-07-27:** the user's Windows-working
URL revealed the real path is **`/onvif1`** (video H.265 + audio PCMA, main) and **`/onvif2`**
(video only, sub) — NOT `/cam/realmonitor` (which is a decoy path returning a broken canned
SDP). With the right path we also nailed the **per-camera password mapping** (firmware answers
DESCRIBE 400 on wrong password, 200 on right): **`.102` (mac aa:bb:cc:dd:ee:01) = `<redacted-pw>`**,
**`.103` (mac aa:bb:cc:dd:ee:02) = `<redacted-pw>`**. (The Windows URL also used a stale IP `.115`
— DHCP had since moved it; MAC identity is exactly why we don't trust IPs.)

BUT SETUP still 400'd, and then go2rtc started getting `dial tcp ...:554 i/o timeout` and
`.103` stopped answering even ICMP — i.e. **the cheap Wi-Fi cameras hang / drop off the network
under connection pressure**. All the rapid scan/probe connections overwhelmed their tiny RTSP
servers. So the streaming blocker is **camera instability**, fixed by a power-cycle + gentle
access, not a protocol/networking bug.

**LESSON → hard requirement:** discovery/probing must be GENTLE — low concurrency, reuse one
connection for OPTIONS/DESCRIBE/SETUP, always send TEARDOWN, throttle, and never hammer. The
current `active_scan.py` uses 128 threads and opens many short-lived sockets; **it must be made
gentle before it runs against real cameras again.**

Registry now holds the corrected data (paths `/onvif1`, correct passwords) in `data/ccg.db`.

**STREAMING FULLY WORKING (2026-07-27, after user power-cycled both cams):** clean single
attempts succeeded. Direct ffprobe needs **`-rtsp_transport udp`** (TCP interleaved gives
"Nonmatching transport in server reply" — these cams don't honor TCP interleave). **go2rtc
native RTSP source works out of the box on `/onvif1`** (it negotiates transport itself) and
restreams cleanly: probing `rtsp://127.0.0.1:8554/<stream>` returned **HEVC 1920x1080 + PCM
A-law 16kHz** for both cameras. Full pipeline validated: camera → go2rtc → clean local
restream (→ ffmpeg record / WebRTC to dashboard). Confirmed the earlier SETUP-400/timeout saga
was purely camera bad-state from over-probing, not protocol/WSL.

Working stream facts:
- `.102` (mac aa:bb:cc:dd:ee:01): `rtsp://admin:<redacted-pw>@192.168.1.101:554/onvif1`
- `.103` (mac aa:bb:cc:dd:ee:02): `rtsp://admin:<redacted-pw>@192.168.1.102:554/onvif1`
- `/onvif1` = main (H.265 1080p + audio), `/onvif2` = sub (video only). Codec HEVC + PCMA.

Next: (1) ~~make `active_scan.py` gentle~~ **DONE** — two-phase scan (moderate port sweep,
then gentle RTSP probe: one reused connection per camera, throttled, 2 cameras at a time);
also **extracted `discovery/rtsp.py`** (RTSP client primitives: parse/auth/`RtspSession`) so
`active_scan` only holds discovery strategy, and added **`discovery/endpoints.py`** (curated
RTSP path whitelist, iSpy-seeded). (2) ~~`media/go2rtc.py`~~ **DONE** — `build_config()` emits
go2rtc config (JSON = valid YAML, no yaml dep, safe URL escaping) from the registry keyed by
MAC-derived stream ids (`cam_<mac>`); `Go2rtc` class manages the process (start/stop/restart/
wait_healthy) and exposes `restream_rtsp_url()` (for the recorder) + `webrtc_page_url()` (for
the dashboard). **Validated end-to-end 2026-07-27:** registry → generated config → go2rtc →
both restreams probe as HEVC 1920x1080 + PCMA 16kHz. `data/` gitignored (config holds plaintext
creds go2rtc needs). (3) ~~recorder off the go2rtc restream~~ **DONE** — `recording/recorder.py`:
one ffmpeg per camera reads the go2rtc restream and writes 60s MP4 segments to
`recordings/<mac>/<YYYY-MM-DD>/<HH>/<ts>.mp4`, indexed in SQLite (`recordings` table).
**`-c:v copy` (near-zero CPU) but audio is transcoded alaw→AAC** — MP4 can't carry the cams'
PCM A-law (tested: MP4+copy fails, MKV+copy has ts issues, `-c:v copy -c:a aac`→MP4 works).
This ffmpeg's segment muxer won't mkdir, so a maintenance thread pre-creates the current+next
hour dir (rollover-safe) and indexes finalized segments. Shared `db.connect()` added (registry
+ recorder use one DB file, separate tables). Validated end-to-end 2026-07-27.

**Storage monitor DONE** — `recording/storage.py`: `StorageMonitor` watches the recordings
filesystem (`shutil.disk_usage`) with two thresholds — **alert** (80%) raises a warning but
keeps recording; **full** (98%) pauses the recorder (`Recorder.pause()`, go2rtc keeps
streaming) and auto-resumes once usage drops below **resume** (95%, hysteresis). Never deletes.
Exposes `state()` (for dashboard/API) and `check()` (actuator); background thread. To support
this the **recorder gained `pause()`/`resume()` + a supervision loop** (maintenance respawns
any crashed ffmpeg → free 24/7 crash-recovery) guarded by a lock. Validated 2026-07-27:
start→pause(ffmpeg dies)→resume(respawns)→stop, plus storage state machine ok/alert/full.

**FastAPI backend DONE** — `main.py` (app + lifespan that brings up go2rtc→recorder→storage on
startup, best-effort so the API serves even if go2rtc's binary is missing; gated by
`autostart_services`), `auth.py` (secret-key → itsdangerous signed timed cookie; `require_auth`
dependency; constant-time key check), `api/routes.py` (login/logout/me; cameras CRUD that
re-syncs go2rtc+recorder; `discovery/scan`; `media/streams`+`restart`; `storage`; `recordings`
timeline + guarded file serving). Passwords never returned (only `has_password`). Verified via
TestClient 2026-07-27: 401-gating, login cookie, CRUD, no password leak, logout.

**Web dashboard DONE** — plain HTML/JS (no build), `frontend/{index.html,style.css,app.js}`,
served by FastAPI (StaticFiles mounted at `/` after routes). Login gate → live grid/single
(iframes to go2rtc's built-in `stream.html?src=`), "Scan network" → candidate cards to add
cameras (default user `admin`), storage banner (polls `/api/storage`), per-camera recordings
playback (segment list → `<video>` from `/api/recordings/file`).

**FULL STACK VALIDATED end-to-end 2026-07-27** via uvicorn: login → go2rtc pulls both cams →
recorder records+indexes → storage OK → API reports `rec=True` + segments. **Gotcha found &
handled:** `DASHBOARD_SECRET_KEY` derives the Fernet key, so changing it after registering
cameras makes stored passwords undecryptable (config emits `rtsp://admin@` with no password →
go2rtc producer fails → recorder gets 404). Now `registry._decrypt` logs a warning; README
documents "set the secret before adding cameras, don't change it after".

**Ports & deployment (2026-07-27):** all services bind **loopback only** (`127.0.0.1`, never
0.0.0.0) in the **32xx** prototype range, per the user's `ports_doc.md` convention (30xx/31xx
already taken). Allocation (see `.env` / `config.py`): app **3200**, go2rtc API **3201**,
WebRTC **3202**, RTSP restream **3203**. Config: `host/port`, `go2rtc_api/go2rtc_host/
go2rtc_rtsp_port/go2rtc_webrtc_port` (env-overridable). `go2rtc.build_config` binds `go2rtc_host`
on those ports; recorder consumes `restream_rtsp_url` (3203). Run: `python -m backend.app.main`
(uvicorn from settings, loopback). Verified live: all four listen on 127.0.0.1 only, recording
works. **Docker:** `Dockerfile` + `docker-compose.yml` are a SKELETON (not used yet — still
uvicorn on host); open items noted in the compose file (go2rtc needs host networking for LAN
cameras; app needs an "external go2rtc" mode so it doesn't spawn the binary in-container).

**Next:** ONVIF capability probe (`discovery/onvif_probe.py`) for PTZ/audio/mic/LED UI gating;
optional S3 tiering; custom-panel view; finish the Docker path when ready.
- Package `__init__.py` files; `recordings/` dir.
- Memory files saved (see `~/.claude/projects/.../memory/`).

**Confirmed environment facts (2026-07-27):** WSL mirrored is ON (local iface `192.168.1.10`,
a real LAN IP). The two cameras live on `192.168.1.0/24`. Need per-camera credentials to resolve
the real RTSP path (401 → 200/404) and to run ONVIF SOAP.

**Repo layout:**
```
pyproject.toml  .env.example  .gitignore  README.md
docs/DECISIONS.md
backend/app/
  config.py
  discovery/ws_discovery.py
  media/  recording/  db/  api/   (empty, planned)
recordings/
```

---

## 6. Next actions (in order)

1. ~~Enable mirrored~~ **DONE.** ~~Test WS-Discovery~~ **DONE (empty).** ~~Active-scan fallback~~
   **DONE — found 2 cameras.**
2. **Resolve real RTSP paths:** re-run `python -m backend.app.discovery.active_scan
   --user <u> --password <p>` once the user provides credentials → the DESCRIBE turns each
   401 into 200 (real path) or 404 (wrong). Record the working per-camera stream URL.
3. **Build ONVIF capability probe** (`discovery/onvif_probe.py`): SOAP over httpx with
   WS-UsernameToken digest auth → `GetCapabilities`, `GetServices`, `GetStreamUri`. Returns a
   per-camera feature map + RTSP URL. (Can be written now; needs live camera to test.)
4. **go2rtc integration** (`media/go2rtc.py`): bootstrap/download binary, generate config from
   discovered RTSP URLs, expose WebRTC to the dashboard.
5. **Recorder** (`recording/recorder.py`): ffmpeg `-c copy` segment per camera + SQLite index.
6. **Storage monitor** (`recording/storage.py`): 80% alert, skip-save-when-full, S3 tiering.
7. **FastAPI app + auth** (`main.py`, `api/routes.py`, secret-key cookie).
8. **Frontend dashboard** (grid / single / custom panel).
9. **Device control:** PTZ, audio, mic, LED/siren via ONVIF.

---

## 7. Open questions / risks to revisit

- **Per-camera credentials:** store keyed by MAC (see identity decision). Still open: at-rest
  encryption approach (e.g. Fernet with a key derived from the `.env` secret). Fields per camera:
  MAC, friendly name, username (default `admin`), password, confirmed stream path, last-seen IP.
- **Cheap cloud-only cams** (Tuya/XMEye variants) may not expose usable RTSP/ONVIF — user's do,
  but "generic support" goal means we must degrade gracefully when they don't.
- **LED/siren control** is often vendor-specific (not always ONVIF DeviceIO) — may need per-brand
  handlers.
- **Multi-homed hosts:** current WS-Discovery sends on the primary interface only; may need
  per-interface probing later.
- **Frontend framework** not finalized (user: "pouco importa / low-cost"). Leaning Vite static.
- **Python 3.14** is very new — watch for missing wheels in deps.

---

## 8. Rename → Community Cam Guard (CCG) + camera profiles (2026-07-27)

Renamed the project off the "Yoosee/yousee" branding — legal safety (avoid a brand name that
isn't ours) and SEO/discoverability for the open-source repo. **Name: Community Cam Guard
(CCG)**; package/repo slug `community-cam-guard`. Yoosee stays mentioned as a *supported camera*
(README + `yoosee` profile), not as the project's identity.

Applied: `pyproject.name`, FastAPI title, cookie `ccg_session`, DB `data/ccg.db` (renamed the
existing file to keep registered cameras), USER_AGENT, Docker `ccg_app`/`ccg_go2rtc` +
image `community-cam-guard-app`, compose project `ccg`, README rebrand (generic-first, Yoosee
as example, SEO keywords) and frontend titles. The local checkout folder name is not the project
identity — the repo/package is `community-cam-guard`.

**Extensibility (contributor-friendly):** the ad-hoc RTSP whitelist `discovery/endpoints.py`
became **`discovery/profiles.py`** — a list of `CameraProfile`s (key, label, ordered RTSP path
templates, transport hint, notes). Discovery probes the union of all profiles. Adding a camera
family = append one `CameraProfile`; no engine changes. Profiles today: yoosee, xiongmai, dahua,
hikvision, generic. This is the documented place for new-model PRs.

---

## 9. Planned: recordings browser + dashboard polish (2026-07-27)

**Dashboard visual pass (done):** header rebuilt with a brand mark, a segmented Grid/Single
toggle, icon buttons (SVG sprite in `index.html`), primary/gradient buttons, polished login.
Grid divides the viewport equally for up to 4 cameras (1→full, 2→split, 3–4→2×2) and scrolls
beyond 4; Single has a scrollable camera rail to switch the large view. `frontend/` is bind-
mounted in compose so UI tweaks are live (no rebuild).

**Recordings browser (planned):** a dedicated "recover recordings" screen — filter by camera +
time period, list the segments for that window. **Hard requirement: paginate and cap results**
(bounded page size, load-on-demand/next-page) so a large library never triggers hundreds of
requests or one huge response — protects our own services and any third-party (e.g. future S3).
Implies extending `/api/recordings` with `limit`/`offset` + a time range (start/end), and the DB
query already has the `idx_recordings_mac_day` index to keep it cheap. Playback stays via the
existing guarded `/api/recordings/file`.

---

## 10. Device control (PTZ, ONVIF on port 5000) + capability probe (2026-07-27)

**Discovery gap (real, now fixed):** a full 1–65535 port scan of our cameras finds only **554
(RTSP)**, **5000**, and **50000** — the old scanner knocked on a fixed `DEFAULT_PORTS` list and
**missed 5000/50000 entirely**. So `active_scan.enumerate_ports()` + `WIDE_PORTS` now deepen the
port picture per *found* host.

**Where PTZ actually lives (took two wrong turns to find):**
1. Ports 5000/50000 answer no plain HTTP/ONVIF to a naive/empty request → they *looked*
   proprietary.
2. The community `wredan/yoosee_camera_control_api` suggested PTZ over RTSP `SET_PARAMETER
   ptzCmd`. Our cameras answer that **200 but never move** (verified by frame-diff vs a still
   baseline). A 200 there is a decoy — **do not use it**.
3. Correct channel: **ONVIF/SOAP over TCP port 5000** at `/onvif/ptz_service`, **without
   WS-Security** (per `victorbillyph/Yoosee-camera-documentation`, confirmed live). PTZ =
   standard `ContinuousMove` (velocity pan `x`/tilt `y` ∈ [-1,1]) then `Stop`; `ProfileToken` =
   `IPCProfilesToken0`. **This moves the hardware for real** — pan and tilt confirmed by frame
   diff (PSNR ~12 vs ~29 still), and an opposite move returns toward origin. So the earlier
   "fixed camera" conclusion was **wrong** — the cameras are full pan/tilt (user confirmed 360°
   via the vendor app).

**Firmware quirk:** this minimal ONVIF stack implements only the motion verbs — `GetConfigurations`
/`GetNodes` just close the socket. So the **non-moving capability probe** is a **zero-velocity
`ContinuousMove`** (returns 200, moves nothing — verified), not a query.

**Control model:** the UI issues discrete **pulses** — `ContinuousMove` in a direction, then a
`Stop` after `PULSE_SECONDS` — so the camera always stops even if the client disconnects (no
runaway pan). Repeat-click to pan further. (Press-and-hold start/stop is a possible future UX.)

**What shipped:**
- `control/ptz.py`: ONVIF `ContinuousMove`/`Stop` over port 5000; `move(camera, direction)` pulse,
  `supports_ptz()` (zero-velocity probe), `velocity_for()` (dir → pan/tilt). New `control/` package.
- `discovery/rtsp.py`: `request(extra_headers=…)` + `parse_sdp()` (audio/video tracks + codecs).
- `discovery/capabilities.py`: PTZ via ONVIF probe; audio/video/codecs via RTSP DESCRIBE SDP;
  `classify_ports()` (rtsp / onvif-ptz(5000) / p2p(50000) / http / unknown).
- `discovery/active_scan.py`: `enumerate_ports(ip)` + `WIDE_PORTS`; `scan(deep_ports=True)`.
- `db/registry.py`: `capabilities` JSON column (idempotent `_MIGRATIONS` ALTER for existing DBs).
- API: `POST /api/cameras/{mac}/probe` and `POST /api/cameras/{mac}/ptz` ({direction});
  capabilities in the camera payload.
- Frontend: on-video PTZ D-pad (gated by `capabilities.ptz`), capability badges, per-camera probe.

**Live-validated end-to-end:** probe of .102 → `{ptz:true, onvif, H265, PCMA, ports 554/5000/50000}`;
PTZ right/left via the API **physically pans the camera** (PSNR ~12) and returns it; bad direction
→ 400, unknown cam → 404, unauth → 401. Tests: 43 pass (SDP parse, velocity map, pulse-then-Stop,
port classify/WIDE_PORTS, caps persistence). See the [[yoosee-ptz-protocol]] memory.

---

## 11. Live audio ("listen") in the dashboard (2026-07-27)

The cameras are **HEVC video + PCMA/G.711 audio** on `/onvif1` (the recorder muxes audio to
AAC in MP4 already). The live dashboard was **silent**, and the root cause is a codec pincer:
- **WebRTC** carries G.711/Opus audio fine but **can't do HEVC video**.
- go2rtc's **MSE** path *can* show HEVC but **won't reliably mux audio alongside it** — so the
  browser gets a video-only track and **disables the volume control**.
- A native `<video>` on `stream.mp4` **can't decode HEVC** in Chrome at all (tried — broke video).

**Fix — a browser-friendly H.264 variant played by go2rtc's own player:**
- `media/go2rtc.py`: for cameras with a known audio track, `build_config` adds a second stream
  `cam_<mac>_web = ffmpeg:cam_<mac>#video=h264#audio=aac#audio=opus`. Transcoding video to
  **H.264** (+ AAC for MSE, Opus for WebRTC) lets go2rtc's player use **WebRTC** — H.264 video +
  audio, low latency, **working volume**. go2rtc resolves `ffmpeg:<stream>` against the base
  stream (one camera connection) and only runs this transcode **while the stream is being
  viewed**. The **base stream is untouched** — the recorder keeps `-c:v copy` on the original
  HEVC (verified: base restream stays HEVC+PCMA; recorder keeps indexing).
- API: `has_audio` + `web_stream_id` on each camera (audio gated on the capability probe).
- Frontend: for cameras with audio, point **go2rtc's own low-latency iframe player** at the
  `_web` stream. With the variant transcoded to **H.264**, the player uses **WebRTC** — video
  **and** audio in one player with a working volume control (unmute to listen). Cameras without
  audio play the base stream. (A native `<video>` on `api/stream.mp4` was tried and rejected:
  it works but adds seconds of latency; the go2rtc/WebRTC player stays near-real-time.)

**Trade-off:** the H.264 transcode costs CPU per *viewed* audio camera (HEVC passthrough would be
free but leaves the browser silent). Acceptable for a handful of cameras and only while watching;
revisit (or make opt-in) if it becomes a load concern.

**Validated:** `_web` restream probes **H.264 + AAC with real audio levels** (max ~-22 dB, not
silence); base stream stays **HEVC + PCMA**; recorder still records; audio is audible in the
browser via the go2rtc WebRTC player. (Several "no sound" reports during development turned out
to be faulty local audio hardware, not the pipeline.) 44 tests pass. Two-way audio ("talk"/
back-channel) is a separate, later effort.

---

## 12. Persistent players — no stream reload on view switch (2026-07-27)

Switching Grid <-> Single reloaded every camera stream (a visible re-buffer), because
`render()` did `stage.innerHTML = ""` and rebuilt the tiles each time — destroying and
recreating the go2rtc `<iframe>`s. (Even *moving* an iframe in the DOM reloads it, so
"reuse but reparent" wouldn't help either.)

Fix: the camera tiles are now **built once and kept mounted** in a persistent `#players`
container; the view is a **pure CSS switch** on `#stage` (`grid` / `single` / `recordings`).
- `renderPlayers()` reconciles tiles with the camera list *without* recreating existing ones
  (new mac -> append; missing -> remove; existing -> refresh only its bar in place). Frames
  are never re-created or re-appended.
- `applyView()` (called by the view buttons and the single-mode rail) only toggles the
  `#stage` class and the `.selected` tile — no DOM teardown, so streams keep playing.
- Single mode keeps the non-selected tiles mounted but `display:none` (alive, not reloaded);
  Recordings hides `#players` entirely and renders into its own `#recordings` container.

Net: flipping views, or clicking through cameras in Single, is instant with no re-buffer.

---

## 13. PTZ press-and-hold + mobile-responsive layout (2026-07-27)

**Press-and-hold PTZ.** Clicking once per nudge (the `step`/pulse from §10) was tedious.
**Camera quirk (measured):** these cameras' ONVIF `ContinuousMove` is really a **fixed step** —
it moves a preset amount and **stops on its own**, ignoring the "continuous" contract (verified:
holding a single `start` with no Stop, the frame stops changing after ~1s). So a lone start only
nudges once. The D-pad therefore **repeats the step on an interval while held**
(`PTZ_REPEAT_MS`≈600ms, <2 req/s — gentle) and stops on release. Backend: `control/ptz.py` gains
`start(camera, dir)` (one ContinuousMove, no auto-stop) and `halt(camera)` (Stop); `POST
/cameras/{mac}/ptz` takes `action` = `start` | `stop` | `step`. Frontend uses **pointer events**
(`pointerdown`→start+interval, `pointerup`/`pointercancel`/`lostpointercapture`→stop) with
`setPointerCapture` so a finger sliding off still stops, an **8s safety auto-stop**, and a
`contextmenu` preventer (no long-press menu on touch).

**Mobile-responsive.** The dashboard is expected to be opened from phones. Below **760px**:
header collapses to icons (labels hidden), the grid becomes **single-column** (`grid-auto-rows:
minmax(210px, 46vh)`), and Single stacks the camera rail as a horizontal strip above the large
player; touch targets grow (PTZ/icon buttons). Below **480px** the per-camera IP and brand
subtitle are hidden to save room. `#dash` uses `100dvh` so mobile browser chrome doesn't clip
the stage. Pure CSS media queries — no JS branching.

---

## 14. Camera software reboot — investigated, SHELVED (roadmap) (2026-07-27)

Goal: reboot a camera in software (no power cycle). The cameras' ONVIF **device service**
(`/onvif/device_service`, port 5000, `tds`) answers `GetDeviceInformation` (model `IPC`, firmware
`40.01.22`), so ONVIF `SystemReboot` looked available. **It isn't** — thoroughly tested, not
assumed:
- `SystemReboot` plain, + WS-Security (UsernameToken/PasswordDigest), and + WS-Addressing
  (`wsa:To`/`wsa:Action` + SOAPAction) — **all** just close the connection and the camera **never
  reboots** (monitored ports 5000 **and** 554 staying up for 45–120s each time).
- The ONVIF stack is partial/buggy: `GetScopes`, `GetSystemDateAndTime`, `SystemReboot` all
  "close connection without response" (= not implemented); only a subset works.
- Port **50000** RSTs on an XM/dvrip login probe and gives no banner → not XM/dvrip; it's the
  vendor **Gwell P2P** channel (cloud-mediated), where the app's reboot really lives.
- Community `victorbillyph/Yoosee-camera-documentation` documents no reboot.

**Decision: shelved to the roadmap.** Unlike PTZ (ONVIF had the answer), here ONVIF is exhausted;
cracking reboot needs a **packet capture of the vendor app rebooting** to reverse the Gwell P2P
command — deferred. What we keep as the foundation: `control/device.py` (`reboot()` = standard
ONVIF `SystemReboot`, correct for *compliant* cameras; `info()` = model/firmware, which works and
is stored in capabilities) and `POST /cameras/{mac}/reboot`. **No reboot button in the UI** (it
would be a dead button on these cams; `capabilities.reboot` was a false signal — GetDeviceInformation
responding ≠ SystemReboot honored — so it was removed).

### Roadmap / pending
- **Camera reboot** on Yoosee/Gwell firmware — needs vendor P2P capture; ONVIF path kept for
  compliant cameras.
- **Two-way audio (talk / speaker out)** — send mic audio to the camera's speaker (next up).
- Broader per-brand/model **profiles** so capabilities & control "plug in" (extend
  `discovery/profiles.py` + the `control/` modules per family).

---

## 15. Pluggable camera **drivers** — the core architecture (2026-07-27)

The project's founding goal is a **generic** dashboard where adding a brand/model reuses the
existing structure. That got buried as Yoosee-specific ONVIF logic accreted in `control/` +
`discovery/capabilities.py`. Refactored into a proper plug-in layer: **`backend/app/drivers/`**.

A **driver** (`CameraDriver` subclass) is the single unit of camera-family knowledge — RTSP
discovery paths, family detection (`matches`), the capability probe hooks, and controls (`ptz`,
`reboot`, ...). Everything else is generic and speaks only to this interface:
- **discovery** (`active_scan`) gets its path list from `drivers.rtsp_paths()` (union of all
  drivers, most-common first).
- **probe** = `drivers.probe(camera, open_ports)` → detects the driver (by vendor + open ports)
  and runs its probe; the generic base already handles the shared RTSP-SDP part (video/audio
  tracks + codecs), families override `_probe_controls`.
- **API** routes PTZ/reboot through `drivers.for_camera(camera)`; unsupported controls raise
  `Unsupported` → HTTP **501** (honest, no dead buttons).
- the camera stores its `driver` key in its capabilities JSON.

The low-level protocol **toolboxes stay shared and reused**: ONVIF SOAP in `control/ptz.py` +
`control/device.py`, RTSP in `discovery/rtsp.py`. A driver just wires them for its family (or
adds a new toolbox for a non-ONVIF brand). `discovery/profiles.py` and
`discovery/capabilities.py` were **removed** — the drivers supersede both.

Shipped drivers: `yoosee` (full — ONVIF-5000 PTZ + device info; reboot/audio-out are Gwell-P2P
and stay `Unsupported`), and `dahua` / `hikvision` / `xiongmai` / `generic` (discovery paths
today; controls are a one-method add when someone tests the hardware). **Adding a brand = one
file in `drivers/` + one line in the registry** — see CONTRIBUTING.md. Live-validated: probe of
our cams reports `driver: yoosee`, PTZ start/stop still works, reboot → 501. 54 tests pass.

---

## 16. Crash-safe recording segments — fragmented MP4 (2026-07-27)

The 24/7 recorder wrote each segment as **plain MP4**, whose `moov` index is appended only on
clean finalize. Measured on a real segment: `ftyp/free/mdat(356 MB)/moov` — the index sits
*after* the whole media, so a hard kill (ffmpeg crash, WSL/host crash — which is exactly how the
prior session died) leaves the in-progress segment with **no `moov` → totally unreadable**; the
whole current chunk is lost, not just its tail.

Fix (one addition to the segment muxer in `recording/recorder.py::_spawn`):
`-segment_format_options movflags=+frag_keyframe+empty_moov+default_base_moof`. Each segment is
now **fragmented MP4**: `moov` up front, media as a chain of self-contained `moof`/`mdat`
fragments flushed on every keyframe. An abrupt kill leaves the segment **playable up to its last
flushed fragment**.

Verified empirically (not assumed): a plain-mp4 segment truncated to 40% → `moov atom not found`,
unreadable; a fragmented segment truncated to 40–55% → `ffprobe` reports a valid duration and it
plays up to the last good fragment. fMP4 keeps everything else intact — native `<video>` playback
in the browser, `-c:v copy`, per-segment seek, the day/hour layout and SQLite indexer are
unchanged. 12 recording/storage tests pass.

**Rejected:** MPEG-TS segments (most truncation-robust) — `.ts` doesn't play natively in a
`<video>` tag, so it would force HLS/transmux or a remux-on-finalize step; fMP4 gives the same
crash-safety while staying browser-native.

---

## 17. ONVIF service sweep on port 5000 — media discovery YES, audio-out NO (2026-07-27)

"Investigate known endpoints on known ports": the field units expose exactly **three** ports
(`554` RTSP, `5000` ONVIF, `50000` Gwell P2P). We had only used the ONVIF **device** + **ptz**
services on 5000. A gentle `GetCapabilities`/`GetServices` sweep found the camera actually
advertises **four** ONVIF services: `device_service`, `ptz_service`, **`media_service`**, and
**`deviceio_service`** (plus `AudioOutputs`/`AudioSources` capability tokens — the hardware has a
speaker + mic).

**Media service works — now used.** `GetProfiles` and `GetStreamUri` answer cleanly (two profiles,
Main/Sub, G711 audio, H264/H265). So the `yoosee` driver's probe now asks the camera for its
**real** RTSP paths via `control/media.py` (`profile_tokens` + `stream_uri` → `stream_paths`)
instead of trusting the hard-coded `rtsp_paths` guesses; live-validated on both units →
`['/onvif1', '/onvif2']`. Result stored in `Capabilities.stream_paths`; empty result falls back to
the hard-coded list, so nothing regresses on non-ONVIF cameras. (`GetStreamUri` wraps the URL in
`<tt:MediaUri>`, so the parser anchors on `<tt:Uri>` specifically.)

**Two-way audio over ONVIF — exhausted, PROVEN (not assumed).** Despite the advertised
`AudioOutputs`, the speaker is unreachable via ONVIF on this firmware:
- Media/DeviceIO **`GetAudioOutputs` / `GetAudioOutputConfigurations` / `GetAudioDecoderConfigurations`**
  all **close the connection without a response** — the same partial-stack signature as
  `SystemReboot` (§14).
- The standard **RTSP backchannel** (`DESCRIBE` with `Require: www.onvif.org/ver20/backchannel: on`)
  is **ignored**: the SDP is byte-identical with/without it and offers no `sendonly` audio track.

So **talk *and* reboot both live only in the Gwell P2P channel (port 50000)** and need a vendor-app
pcap to reverse — now confirmed by evidence, so we stop rabbit-holing ONVIF for them. `media_service`
stays as the concrete win from the sweep; `deviceio_service` audio ops are dead here.

**Also extractable from :5000 (found in the sweep, wiring deferred):**
- **`device.GetNetworkInterfaces`** — TRUSTWORTHY: returns the camera's own MAC (`aa-bb-cc-dd-ee-01`
  / `aa-bb-cc-dd-ee-02`, verified against ARP) + hostname `IPC73` + DHCP/IP → authoritative identity
  **without** ARP/`/proc/net/arp` (which only works under mirrored WSL). This is the one worth wiring
  when identity needs to stop depending on ARP.
- **`media.GetVideoEncoderConfigurations` / `…Options`** — answer 200 but are a **DECOY**: they report
  **H264 720p (Token0) / 480p (Token1)** while the real `/onvif1` stream is **HEVC 1080p** (what we
  actually record). The ONVIF encoder metadata is disconnected from the real HiSilicon encoder, so a
  quality-control feature built on it would be fiction and `SetVideoEncoderConfiguration` almost
  certainly won't actuate the real stream — **do not wire.** (Contrast: `GetStreamUri` paths and the
  MAC above *were* verified against reality; those are safe.)

Dead ends in the sweep (connection-close): `GetSnapshotUri` (no ONVIF JPEG), PTZ
`GetNodes`/`GetPresets`/`GetStatus` (no presets), `imaging` (no brightness/contrast), events
`GetEventProperties` (no native motion topics), `GetOSDs`, `GetUsers`, `GetHostname`.

**Port 50000 (Gwell P2P) — characterized, dead to blind probing (tested).** It **accepts** the TCP
connection but stays **completely silent**: no unprompted banner, and no reply to an HTTP GET or a
zero-frame hello (holds the socket open, doesn't even RST). It's waiting for a specific binary
handshake it only speaks with the vendor app. Confirms §14/§17: **talk + reboot need a pcap of the
Gwell handshake** — there is no probe-our-way-in path.

---

## 18. No-auth camera identification during scan (port-agnostic) (2026-07-28)

Discovery used to surface a found host as IP + open ports + MAC only; model/firmware/driver came
later, in the authenticated post-add probe. But these cams answer the ONVIF device + media
services **without WS-Security**, so we can identify a camera **before the user types any
password** — the whole point of showing a candidate they just attach an RTSP password to.

`active_scan._identify(host)` (runs per host in the gentle probe) reads, credential-free:
- **manufacturer / model / firmware** — `GetDeviceInformation`
- **real RTSP paths** — `GetProfiles` + `GetStreamUri`
- the **driver** — `drivers.detect(vendor + open ports)`

**Port-agnostic — no assumption that ONVIF lives on :5000.** Brands expose ONVIF on different
ports (80/8000/8080/8899/5000/…). We try the SOAP probe across the host's **open** ports (skipping
pure-RTSP media ports, which can't speak SOAP), ordered by an HTTP/ONVIF-likelihood list so we
usually succeed before spending a timeout on a silent binary port (e.g. a P2P channel that accepts
TCP but never replies). First answer wins; the reported RTSP path becomes the candidate's
`suggested_path` (no authed DESCRIBE-200 needed). Gentle + best-effort: generous 10s timeout (these
cheap cams answer ONVIF slowly under load — measured up to ~12s when hammered), and any miss
degrades to driver-from-ports so the camera stays addable. **Only identification is credential-free
— the RTSP stream and control commands still need the password.** `ScannedHost`/`Candidate` carry
the fields; the scan API + "add camera" card surface them; the identified vendor/model persists on
add. Live-validated: a responsive cam reports `driver=yoosee, model=IPC, fw=40.01.22,
paths=[/onvif1,/onvif2]` with no login.

**Deferred to Phase 2 (needs the vendor P2P transport):** auto-provisioning the RTSP password —
setting/rotating it ourselves via the Gwell command channel so the app is fully self-sufficient
(viable especially for unclaimed cameras via the init-password flow). The RTSP password is separate
from the device/app password, so rotating it wouldn't lock out the vendor app.

---

## 19. Browser-playable recordings + a size-index self-heal (2026-07-28)

**Black screen + no audio (fixed).** Segments are recorded **HEVC** (`-c:v copy`, the zero-CPU 24/7
point), but browsers can't decode HEVC in a `<video>` tag — the recordings player showed black, and
the failed video track took the audio down with it. Fix: the `/recordings/file` endpoint serves
**H.264**. New `recording/playback.py` transcodes an HEVC segment on first view to a cached
**faststart** MP4 (`moov` + real duration up front) served via `FileResponse`, so the browser knows
the length and can **seek** — what a recordings reviewer needs; later views hit the cache (instant).
Audio is `-c:a copy` (already AAC); H.264 segments are served as-is; the recorder is unchanged.
A first attempt at *progressive streaming* (fragmented MP4) was **rejected**: it starts faster but
leaves the browser with no duration and no seek — it showed a bogus ~25 s pseudo-duration, worse
for review. Cache lives in `data/playback_cache/` (persisted, gitignored); eviction/pre-transcode
is on the roadmap.

**"0.0 MB" in the list (fixed).** A fragmented-MP4 segment briefly exists as a ~28-byte header-only
stub (`empty_moov` written at segment start, before the first fragment). An early index pass could
record that stub size, and `INSERT OR IGNORE` never updated it → the list showed 0.0 MB for full
~13 MB segments. `recorder._index` now **upserts** (`ON CONFLICT(path) DO UPDATE`) refreshing
`size_bytes` once the file grows/finalizes, only when it changed (no churn for finalized segments) —
which also self-heals rows written before the fix.

---

## 20. Live-player UX: per-camera restart + audio-off in Recordings (2026-07-28)

The original go2rtc live players were **cross-origin iframes**. They have since become same-origin
`<cam-player>` elements for diagnostics and precise recovery (see §34), while retaining the two UX
behaviours introduced here:

- **Per-camera "restart stream"** — a refresh button on each tile now disposes that consumer and
  cycles only the camera's local browser transcode before reconnecting. It does not restart the
  camera, its shared RTSP producer, or recording.
- **Audio off in Recordings** — switching to Recordings only hid the live `#players` via CSS, but a
  hidden player keeps streaming, so both cameras' audio played over the recording. `setPlayersLive()`
  now **unloads** the players when entering Recordings (tears down stream +
  audio) and rebuilds them on the way back to Grid/Single. Grid↔Single stay live (no-op, no
  re-buffer); tiles created while in Recordings start unloaded. Returning to live re-connects the
  streams (a small, expected re-buffer, since you'd left the live view).

---

## 21. Playback-cache eviction — bounded LRU (2026-07-28)

§19 left the HEVC→H.264 transcode cache (`data/playback_cache/`) **unbounded**: every distinct
segment ever opened stays forever. On a 24/7 recorder whose disk is already watched by the storage
monitor, that silently competes with recordings for space — the storage policy never deletes, so an
unbounded derived cache is the one thing that *would* eat the disk. Fixed with a size-capped LRU.

- **Cap** = `PLAYBACK_CACHE_MB` (default **2048 MB**); `0` disables eviction (unbounded, old
  behaviour). Only the derived transcodes are ever touched — **never** a source recording (every
  cache entry is losslessly reproducible from its segment on the next view).
- **LRU by mtime.** A cache *hit* refreshes the file's mtime (`os.utime`), so "recently watched"
  survives; the actual read (`FileResponse`) can't be relied on for atime (volumes are often
  `noatime`), hence the explicit touch.
- **When.** Eviction runs right after a new transcode is promoted into the cache (`_evict(keep=…)`),
  so the cache is trimmed exactly when it grows. No background thread — the write path is the only
  thing that enlarges it. The freshly-written file we're about to serve is passed as `keep` and is
  never evicted, even if it alone exceeds the cap.
- Best-effort + concurrency-safe: `stat`/`unlink` errors just skip a file (a parallel request may
  have already removed it); oldest-first deletion stops as soon as the total is back under the cap.

3 tests added (LRU victim selection, `keep` protection, cap=0 disabled) → 76 pass. `.env.example`
documents the knob. Deferred (still roadmap): background **pre-transcode** of new segments so the
first view isn't the one that pays the transcode latency.

---

## 22. Recording retention — time-based cleanup job (2026-07-28)

Storage was self-limiting only by the disk-full pause (§4): footage grew forever until the monitor
stopped *saving*. Added an explicit, user-configured **retention window**: `RECORDING_RETENTION_DAYS`
(default **7**) — the deliberate counterpart to the storage monitor, which by policy **never
deletes**. This is the one place the app removes footage, and only because the user asked it to by
setting a window.

- **Config.** Integer days; **floor 0 = keep forever** (job disabled, old behaviour); **no upper
  bound** — you keep as much as the disk holds (the storage monitor still guards the ceiling). The
  two mechanisms are orthogonal: retention trims by *age*, the monitor pauses by *fullness*.
- **`recording/retention.py::RetentionCleaner`.** A background thread (mirrors `StorageMonitor`)
  runs **sporadically — hourly** (`DEFAULT_INTERVAL=3600`; retention is day-granular so a tighter
  sweep is pointless). Each pass deletes every segment whose `started_at` is older than
  `now − N days`: the **file**, its **playback-cache transcode** (`playback.cache_path`), and its
  **index row**, then prunes the empty `<mac>/<day>/<hour>` and `<mac>/<day>` dirs (the recorder
  recreates current dirs on demand).
- **DB-driven** — the recordings index is authoritative, so retention removes exactly what the
  recordings page can list; a not-yet-indexed segment is by definition recent and never in scope.
  A file that can't be unlinked keeps its row, so the next pass retries it (self-healing).
- Started in the lifespan after the storage monitor (gated by `autostart_services`; `start()` is a
  no-op when disabled, so no idle thread). This originally used naive local timestamps; §35 and
  ADR 0016 supersede that detail with explicit UTC paths, index values and cutoff.

6 tests added (expiry vs. recent, empty-dir prune, cache drop, 0=keep-forever, floor, disabled
no-op) → 82 pass. `.env.example` documents the knob; set to 7 days for this deployment.

**UI surface (2026-07-28).** The Recordings page now shows the active window so the user knows why
old clips disappear: `/api/recordings` carries `retention_days` (page context; the pure
`query_segments` is unchanged — the route appends it from settings), and the recordings filter bar
renders a localised note — `Retention: N days` / `Retenção: N dias`, or `unlimited`/`ilimitada` when
`0` — with a tooltip explaining auto-deletion. 1 test added (route returns `retention_days`, 0
included) → 90 pass.

---

## 23. ARP-independent identity — MAC from ONVIF GetNetworkInterfaces (2026-07-28)

§17 found `device.GetNetworkInterfaces` returns the camera's **own MAC** (verified against ARP) and
flagged it as "the one worth wiring when identity needs to stop depending on ARP". Wired now.

Camera identity is the **MAC**, not the DHCP-mutable IP. Until now the MAC came only from
`/proc/net/arp` (`active_scan._mac_for`), which reads the kernel ARP cache — and that **only works
on the same L2 segment**, i.e. WSL in `mirrored` mode (a fragility the project has worried about
throughout). A non-mirrored or containerised deployment would then have **no MAC → no stable
identity**.

- **`control/device.py::mac_address(ip, port)`** — posts no-auth `GetNetworkInterfaces` to the
  ONVIF device service and parses `<tt:HwAddress>`, normalised to lower-case `aa:bb:cc:dd:ee:ff`
  (`_normalize_mac` accepts the firmware's dash form `F4-E2-5D-…`, colon form, or bare hex; rejects
  non-12-hex and all-zero; picks the first interface with a real address).
- **`active_scan._identify`** now, on the port that answered `GetDeviceInformation` (still
  **port-agnostic** — see §18), also reads the ONVIF MAC and **prefers it over the ARP value**
  (`host.mac = onvif_mac`), falling back to ARP when ONVIF doesn't report one. So identity is now
  authoritative and works off-LAN/containerised; ARP remains a best-effort fallback.

The camera reports MAC credential-free (same no-auth device service as identity, §18), so this costs
one extra SOAP call on the already-found ONVIF port — no new port probing, no new latency budget.
7 tests added (normalise dash/colon/hex + reject bad/zero, parse HwAddress, skip zero-iface, service
absent, `_identify` prefers ONVIF MAC / keeps ARP fallback) → 89 pass. (Not yet wired: using it to
*re-key* an already-registered camera; today it just improves the scanned-candidate identity.)

---

## 24. Dashboard i18n — en default + pt-BR, no build step (2026-07-28)

The dashboard shipped en-US (§ roadmap). Added a **localisation layer** so it reads in the user's
language while keeping English as the open-source default. No framework/build — it stays a plain
HTML/JS app.

- **`frontend/i18n.js`** — a `STRINGS` dict (`en`, `pt-BR`), a `t(key, params)` that fills
  `{token}`s and **falls back** en→key so a missing translation never blanks the UI, and
  `applyI18n()` that fills static markup via `data-i18n` (textContent), `data-i18n-ph`
  (placeholder) and `data-i18n-title` (title). This originally exposed globals on `window`; ADR 0018
  later replaced that integration with native ES Module exports while retaining zero build tooling.
- **Static strings** carry `data-i18n*` attributes in `index.html`; **dynamic strings** in the frontend
  (camera bars, scan modal, recordings, PTZ/hover titles, storage banner) go through `t()`. The
  product name (*Community Cam Guard* / *CCG*) is intentionally **not** translated.
- **Language pick** — `localStorage` (`ccg_lang`) wins, else the **browser** language (`pt*` →
  `pt-BR`), else `en`. A header `<select>` switches live: `applyI18n()` re-fills static labels and,
  when the dashboard is up, `render()`+`loadStorage()` rebuild the dynamic strings — no reload.
- **Adding a locale = one dict block** in `STRINGS` (61 keys; en↔pt-BR parity verified). No pytest
  (frontend); validated with `node --check`, a `t()` smoke test (interpolation, fallback, switch),
  key-parity + used-vs-defined cross-checks, and serving the app hardware-less (`data-i18n` markup +
  `i18n.js` load order confirmed over HTTP). Design note: the RTSP/ONVIF *paths* and codec tokens
  (e.g. `/onvif1`, `H265`, `PTZ`) stay verbatim — they're protocol identifiers, not prose.

---

## 25. Playback pre-transcode — opt-in cache warmer (2026-07-28)

§19/§21 left on-demand transcoding: the **first** view of an HEVC clip in Recordings waits for
ffmpeg (later views hit the cache). §21 flagged a background pre-transcode as the follow-up — built
now as **`playback.Warmer`**, gated by **`PLAYBACK_PRETRANSCODE`** (default **off**).

**Off by default on purpose.** The recorder is zero-CPU (`-c:v copy`); a warmer transcodes
continuously, so it only makes sense on a box with spare CPU. Enabling it is a deliberate
CPU-for-latency trade.

- **Gentle + bounded.** A background thread (mirrors the other workers) does **one segment per
  tick**, **newest-first** (`recorder.query_segments`, limited to the `window` newest = what a
  reviewer opens first). After warming one it loops back in 2s; when there's nothing to do it idles
  a full interval.
- **Never fights eviction.** It stops transcoding once the cache reaches **90% of
  `PLAYBACK_CACHE_MB`** (`HEADROOM`), so the LRU cap (§21) still bounds total size and we avoid a
  transcode↔evict churn loop. Newest-first warming + LRU-keeps-newest are aligned, not opposed.
- Reuses `transcoded_path` (same cache + eviction path as on-demand), so a segment is never
  transcoded twice and a manual view of a pre-warmed clip is instant. `_next_segment` skips
  already-cached and already-browser-playable (H.264) segments. Started in the lifespan after the
  retention job; `start()` is a no-op when disabled (no idle thread).

4 tests added (disabled no-op, warms newest uncached HEVC, skips cached/H.264, stops before cap) →
94 pass. `.env.example` documents the knob; hardware-less boot confirms the wiring (warmer present,
disabled).

---

## 26. Bugfix: add/delete 500'd under compose (external go2rtc) (2026-07-28)

**Symptom (found live-testing the add/remove flow).** The dashboard "remove" button did nothing:
the confirm popped, the camera vanished from the registry, but the tiles kept streaming. Every
camera mutation returned **HTTP 500**.

**Root cause.** `_resync` (run after add/delete) called `media.restart()`, whose old body was
unconditionally `stop(); start()`. `start()` spawns the **go2rtc binary** — but under Docker Compose
go2rtc runs as its **own container** (`MANAGE_GO2RTC=false`) and there is no binary in the app
image, so it raised `FileNotFoundError: go2rtc binary not found at bin/go2rtc` → 500. The registry
delete had already run (so the camera was gone), but the 500 made the frontend `api()` throw →
`loadCameras()` never ran (stale tiles) and go2rtc was never reconfigured (kept the dropped stream).
The startup path already branched on `manage_go2rtc`; **only `_resync` didn't** — so this hit
*every* add and delete under compose, not just remove.

**Fix (two layers).**
1. **`Go2rtc.restart()` is mode-aware.** Managed (we own the binary): `stop(); start()` as before.
   External: **rewrite the config and `POST /api/restart`** to the running go2rtc (new
   `reload_external()`) so it re-reads the mounted config — never spawn a missing binary. go2rtc has
   no file-watch hot-reload, so the explicit reload is required. `Go2rtc` gained `manage`
   (from `manage_go2rtc`, overridable for tests).
2. **`_resync` is best-effort.** The registry write has already committed, so a hiccup
   reconfiguring live services now logs a warning instead of 500-ing the CRUD call.

Verified live: rebuilt the app image, `DELETE` now returns **200**, go2rtc reloads to an empty
stream set (the stale stream cleared), registry empty. 5 tests added (managed re-exec vs external
reload-never-spawn, `reload_external` POST + error path, `_resync` swallows a media error) → 99 pass.

---

## 27. Dedicated "Cameras" tab for scan + configuration (2026-07-28)

The scan/add flow lived in a **modal**. Replaced with a dedicated **Cameras** view (4th segment:
Grid | Single | Recordings | Cameras) so setup is a first-class screen, not a popup — the user's
pick over injecting config cards into the live grid (which would mix *monitoring* with *setup* and
fight the grid's count-based layout math).

- **A grid of cards.** Configured cameras render as a **manage card** (identity + recording status +
  probe/remove); discovered (unconfigured) cameras render as a **config card** — the no-auth ONVIF
  identity (vendor/model/fw/driver, §18/§23) + credential inputs + Add. `removeBtn`/`probeBtn` were
  extracted from the live `camBar` and reused by the manage card.
- **Filter** `All / Available / Configured` (a segmented control) — `state.camFilter` picks which
  cards show, so a big mixed list stays legible.
- **Scan in-place.** `runScan()` switches to the tab, shows the loading spinner (§ scan popup) while
  the ~30s scan runs, then renders candidates into the grid; `state.candidates` holds them.
  Adding one drops it from `candidates`, re-loads the configured list, and re-renders — no reload.
  The header **Scan network** button now just calls `runScan()` (→ the tab). The old modal DOM +
  `openModal` were removed.
- **Live players are unloaded off the live views.** `setPlayersLive` is now `grid || single` only
  (was `!== recordings`), so entering Cameras (or Recordings) tears down the cross-origin iframes —
  no stream/audio running under the setup screen.
- i18n: 9 new keys (`nav.cameras*`, `cams.filter_*`, `cams.none*`, `cams.scanFailed`), en↔pt-BR
  parity 73/73. New `#i-cam` sprite for the tab.

Frontend-only (bind-mounted, no rebuild). Validated: `node --check`, parity + used-vs-defined
cross-checks, served-markup check, and a **live end-to-end scan** against the running stack — 2
candidates came back fully identified no-auth (`Technology IPC fw 40.01.22`, driver `yoosee`,
`/onvif1`), confirming the §26 go2rtc fix didn't regress discovery.

---

## 28. Auto-probe capabilities on add (2026-07-28)

Adding a camera stored its credentials but **not** its capabilities — so PTZ/audio controls stayed
dark until the user clicked the separate "probe" button. Capabilities should be discovered as part
of *configuring* the camera. Now `POST /cameras` runs the capability probe right after the registry
write: driver detection + PTZ (ONVIF) + audio/video codecs (RTSP SDP) + port roles, stored on the
camera, so controls light up immediately.

- Extracted `_probe_and_store(cam)` (shared with the manual `POST /cameras/{mac}/probe`).
- **Best-effort**: gated on `cam.last_ip`, wrapped in try/except — a slow or failed probe (these
  cheap cams can answer ONVIF slowly) must not fail the add; the camera is saved and re-probable by
  hand. The manual probe button stays as a re-probe.
- Frontend: the Add button shows `Adding…` while the request (which now includes the probe, ~a few
  seconds) runs.

3 tests (auto-probes + stores on add, skips probe with no IP, swallows a probe failure) → 102 pass.
Verified live (rebuilt image): re-adding `.102` returned full caps (`ptz:true`, `ptz_protocol:onvif`,
`H265`/`PCMA`, ports) in one step; both field cameras now report `ptz=True, audio=True`. Note:
cameras added *before* this change keep empty caps until re-added or hand-probed (not retroactive).

---

## 29. Validate RTSP credentials on add — reject a wrong password (2026-07-28)

Bug: adding a camera with the **wrong password** was accepted (200) and stored — the camera then
never streamed. The §28 capability probe is best-effort (swallows failures so an offline camera
stays addable), so a bad password sailed through.

Fix: `POST /cameras` now verifies the RTSP credentials **before** saving, via new
`rtsp.check_credentials(ip, port, path, user, pw)` → `"ok" | "auth" | "unreachable"`. A definitive
`"auth"` returns **401** and the camera is not saved (validation runs before the upsert, so an
existing record is untouched); `"unreachable"` (offline/ambiguous) is allowed so a temporarily
offline camera stays addable. The frontend shows the error inline in the config card.

**The tricky part (found live, not assumed).** These cameras don't answer a wrong password with
another 401 — they reply **`400 Bad Request`** to the *authenticated* DESCRIBE (right password →
`200`). A first cut keyed on 401 classified the 400 as "unreachable" and let it through. So the rule
is: once the camera issues its 401 challenge and we answer it, **only 200 means the credentials are
good; any other status is a rejection** (`"auth"`). Connection drops with no reply stay
`"unreachable"`. Verified live against `.102`: right password → `ok`, wrong → `auth`.

7 tests (5 `check_credentials` incl. the 400-after-challenge case + no-username challenge; route
rejects wrong creds/not saved; unreachable still added) → 111 pass.

**Follow-up: the rejection is HTTP 422, not 401.** First cut returned **401**, but the dashboard's
`api()` treats *any* 401 as a dead session and redirects to the login screen — so a wrong *camera*
password bounced the user out of the app (and, since the session cookie was still valid, a refresh
"logged them back in", which looked bizarre). A bad camera password is a validation error on the
request body, not a dashboard-session failure, so it's now **422 Unprocessable Entity** (401 stays
reserved for `require_auth`). The config card shows the detail inline; the user stays put.

---

## 30. PTZ latency — fire-and-forget the ONVIF motion verbs (2026-07-28)

PTZ felt sluggish vs. the vendor app's near-instant P2P control. **Measured** the ONVIF path
(`ContinuousMove` on port 5000): the TCP connect is fast (~10ms) but the camera takes **~700ms to
*respond*** to a motion verb (TTFB), and it doesn't honour keep-alive (closes after each response).
We were **blocking on that response** (`urllib.urlopen`) before returning, so every command — and
every 450ms press-and-hold repeat — ate ~0.7s, serialising motion.

Key insight: the camera **acts on the command as it arrives** (a `ContinuousMove` produces its fixed
~0.4s step, which finishes *before* the 700ms HTTP response even lands), so waiting for the response
buys nothing but latency. Fix: send the motion verbs **fire-and-forget** — new
`ptz._send_soap_nowait()` writes the SOAP request over a raw socket, `shutdown(SHUT_WR)` (FIN =
request complete → the camera processes it), and drops the socket without reading the reply.
`_continuous_move(..., wait=False)`, `start()`, `move()` and `stop()` use it (`stop` doubly so —
this firmware ignores Stop anyway, so there's no reason to block on it). The capability probe
(`supports_ptz`) keeps the blocking path — it needs the real 200 and runs off the hot path.

Measured after: `start()` **~3ms** (was ~700ms), end-to-end via the API **~7–15ms** (was ~700ms+) —
a ~200× drop in dispatch latency, so PTZ starts moving effectively on press. 111 tests pass (PTZ
tests unchanged — they monkeypatch `_continuous_move`, which still takes the `wait` kwarg). See
[[yoosee-ptz-protocol]]. (True *continuous* smoothness would still need the Gwell P2P channel, but
the perceived delay is gone.)

---

## 31. Re-key a camera to its authoritative MAC + retroactive capability backfill (2026-07-28)

Two gaps left explicitly open by §23 and §28, closed together because both surface on the same
code path (a scan reconciling against the registry).

**§23 follow-up — re-keying.** §23 taught the scan to prefer the camera's own MAC (ONVIF
`GetNetworkInterfaces`) over the ARP-derived one, but only for *scanned candidates*: a camera
already registered under its ARP MAC stayed there. Once ONVIF answered, the same physical camera
came back with a different key, so `reconcile()` saw an unknown MAC and offered it as a **brand-new
candidate** while the original record — name, password, capabilities — went stale and unmatchable.

- `ScannedHost` now carries **`arp_mac`** alongside `mac`. `_identify` still overwrites `mac` with
  the authoritative ONVIF value, but no longer destroys the ARP one, which is what lets the registry
  recognise the old identity.
- New `registry.rekey_camera(old, new)` moves the row's primary key, preserving everything. It
  **refuses** when the target MAC is already registered — that's a genuinely different camera, and
  merging would silently discard one record's credentials.
- `reconcile()` re-keys when the ONVIF MAC is unknown but the ARP MAC is registered, and reports the
  move through an **`on_rekey(old, new)`** callback. The callback (rather than a direct call) keeps
  the layering honest: `recorder` already imports `registry`, so the registry cannot import the
  recording layer back — the caller wires the two together.
- **Recordings follow the camera.** `recorder.rekey_segments(old, new)` renames
  `recordings/<safemac>/` and repoints the index rows (MAC + path prefix). Without it a re-keyed
  camera loses its whole history, since the browser filters by MAC. Non-destructive: if the
  destination directory already exists nothing is renamed and the index is untouched.
- The scan route calls `_resync` when anything moved — go2rtc streams and recorder processes are
  keyed by MAC.

**§28 follow-up — retroactive capabilities.** Probe-on-add was not retroactive, so cameras added
earlier kept empty capabilities and their PTZ/audio controls stayed dark until someone pressed
"probe" by hand. The scan now backfills them: any *configured* camera that comes back with no
capabilities and a known IP is probed via the shared `_probe_and_store`. A scan is the right moment
— the camera just answered and this is already the slow, user-initiated path — and it stays
best-effort per camera, so a cheap camera timing out never fails the scan.

8 tests (re-key preserves credentials / refuses an occupied target / unknown source; reconcile
re-keys instead of duplicating and leaves a real new camera as a candidate; segment move on disk +
index, destination-exists refusal, same-MAC no-op) → **119 pass**. Smoke-tested through the real
scan route: the ARP-keyed camera came back under its ONVIF MAC with its name and password intact,
no duplicate candidate, and the empty-capability backfill fired.

**Also closed: the "bogus segment duration" debt.** Long carried as known debt (ffprobe reporting
~53 000 s on finalized segments, from `-c copy` inheriting the cameras' wall-clock PTS). It was
already fixed in the recorder (`-reset_timestamps 1` plus `-af aresample=async=1:first_pts=0`) and
verified here against live segments: 301 s, 301 s, 51 s — correct and seekable. The note was stale.

---

## 32. Digital zoom — because there is no optical zoom to drive (2026-07-28)

Zoom was asked for as an indispensable feature. Before building anything, **measured whether the
hardware has it** (same frame-diff method that proved PTZ real in §13, and that exposed the RTSP
`ptzCmd` decoy):

| test | PSNR | reading |
|---|---:|---|
| still scene, no command | 27.9 | baseline |
| ONVIF `ContinuousMove` **Zoom x=1.0**, 5 repeats | 28.2 | **unchanged — no actuation** |
| ONVIF `ContinuousMove` PanTilt (positive control) | 13.4 | collapses — real motion |

So the ONVIF **Zoom verb answers 200 and is a decoy**, exactly like the RTSP `ptzCmd` path. The
control is not lost in translation either: PTZ `GetNodes`/`GetConfigurations` close the connection
without responding (not implemented), and `GetServices` advertises only the *events* service — this
firmware's ONVIF is minimal. The APK agrees: `ZoomView`/`_OnGesture`/`setRenderScaleType` are
**renderer** calls, so the vendor app's own zoom is client-side. **Conclusion: zoom must be digital.**

Implemented as a CSS transform on the player. The go2rtc player is a **cross-origin iframe** we
cannot script — but transforming the iframe *element* needs no access to its content, so the whole
feature is a `translate(...) scale(...)` on `.cam .frame` plus clamped pan offsets.

- `.cam` already had `overflow: hidden`, which clips the scaled frame at the tile edge; the bar got
  `position: relative; z-index: 2` and an opaque background so a zoomed image never spills over the
  controls (a transform doesn't re-layout, so it would otherwise paint across them).
- **`.zoom-overlay`** carries drag-to-pan, wheel-to-zoom (about the pointer, so what is under the
  cursor stays put) and double-click-to-reset. It is `pointer-events: none` **at 1×** so the go2rtc
  player keeps its own controls (clicking to unmute for the listen-in feature) and only takes the
  pointer once zoomed — the compromise that lets both interactions coexist on one cross-origin embed.
- Pan is clamped to `(scale-1)/2` per side, so the user can never drag empty space into view;
  returning to 1× re-centres.
- State is per-camera in a `zooms` map, and `camFrame()` re-applies it, so a **player restart** or
  a Recordings→Grid resume keeps the zoom instead of snapping back.
- Bar controls (`−` / level / `+` / reset) join the quick-action cluster. Unlike PTZ they are **not**
  capability-gated: digital zoom is pure rendering and works on every camera.

Frontend-only (bind-mounted, no rebuild). `node --check` clean, i18n parity 78/78 (3 new keys).

---

## 33. Recorder: bounded ffmpeg — a muxing-queue balloon was OOM-killing the host (2026-07-28)

**Symptom:** "I open WSL, a few seconds pass, and the session closes by itself." Not a shell or
agent problem — it was this project.

**Chain:** the cameras hand the RTSP demuxer packets it cannot timestamp (`pts has no value` /
`Timestamps are unset in a packet for stream 0`). With `-c:v copy` that propagates untouched to
the mp4 muxer, which then cannot interleave the copied video against the AAC track and parks whole
GOPs in its muxing queue — no ceiling, growing to **~2 GB RSS**. The container had **no
`mem_limit`**, so the blowup was a *global* kernel OOM (`constraint=CONSTRAINT_NONE`) rather than a
cgroup one: the kernel was free to pick a victim **outside** the container and killed the WSL
session's `dbus-daemon`, dropping the console. The 5 s supervisor respawned ffmpeg, and it looped.
Collateral: `data/rec_*.log` had reached **88 MB + 69 MB** of the same warning, never rotated.

**Fix, in four layers** — root cause first, then containment so no future variant can reach the host:

1. **Timestamps (the actual cause).** `-use_wallclock_as_timestamps 1 -fflags +genpts` stamps every
   packet on arrival, giving video and audio one monotonic base. Measured against the live restream:
   the old args emit the `pts has no value` family, the new args emit **zero stderr**, with segment
   durations and track start times unchanged (so seeking still works — cf. the `aresample
   first_pts=0` reasoning, which stays). Chosen over recording the `_web` variant (§ costs a
   transcode, kills `-c:v copy`) or dropping audio (loses a real feature) — neither was needed.
2. **Ceilings on the buffers.** `-max_muxing_queue_size 1024`,
   `-muxing_queue_data_threshold 32M`, `-rtbufsize 32M`, `-max_delay 500000`. Hitting one aborts
   ffmpeg; a clean death plus a respawn beats an OOM.
3. **A supervisor that reaps runaways.** `_watchdog_locked` reads each child's RSS from
   `/proc/<pid>/statm` and kills anything past **256 MB** (a healthy remux measures ~56 MB), and
   respawns now use **exponential backoff** (5→120 s, reset after a 60 s healthy run) instead of a
   fixed 5 s tight loop. Logs are truncated in place past 8 MB — safe because the writer holds them
   `O_APPEND`. Level dropped `warning`→`error`.
4. **A cgroup cap** in `docker-compose.yml` (`app` 1g, `go2rtc` 512m, `memswap_limit` equal so it
   fails fast instead of thrashing a 6 GB WSL's swap). This is the load-bearing one: **with a cap,
   the same runaway can only kill the container's own process, never a host process.** Works fine
   under `network_mode: host`.

Layer 3's state machine is covered in `tests/test_recorder_supervisor.py` (13 tests) — which caught
a real bug on first run: `_terminate_all_locked` only cleared macs still holding a process, so a mac
*pending* a backed-off respawn kept its penalty across a pause/resume.

**Out of scope, for the host owner:** the WSL is capped at 6 GB / 2 vCPU with ~22 containers on a
32 GB machine. The fixes above make the recorder well-behaved, but the margin at boot is thin —
raising `.wslconfig` to `processors=4` and more RAM is still worth doing.

---

## 34. Live view: stop transcoding video — passthrough is ~17× cheaper (2026-07-28)

**User report:** "the player reloads constantly; any mediocre app does 10 cameras at 1080p and we
can't do 2 — the technology choices were bad." Correct, and the measurements back it.

**What was wrong.** §11–15 added the `_web` variant as `ffmpeg:<sid>#video=h264#audio=aac#audio=opus`
so the dashboard could use **WebRTC**, which cannot carry HEVC. That decision quietly made a **full
1080p HEVC→H.264 re-encode the default for every viewed camera**. Measured on the 2-vCPU host:

| | CPU (2 cams) | go2rtc RSS | `cpu.pressure` full avg10 | output |
|---|---:|---:|---:|---|
| `#video=h264#audio=aac#audio=opus` | **122%** | 257 MB | **29.59** | H.264 1080p |
| `#video=copy#audio=aac` | **7.1%** | 36 MB | **1.91** | HEVC 1080p (native) |

`cpu.pressure full` is the smoking gun: ~30% of every 10 s window the whole go2rtc cgroup was
*stalled waiting for CPU*. A realtime media pipeline that stalls that hard starves its consumer,
and the player reconnects — exactly the reported symptom. At ~2.7% per camera the new path leaves
room for ~10 cameras in the budget the old one spent on two.

**The fix is to re-encode nothing.** MP4/MSE genuinely cannot carry the cameras' PCM A-law, so the
audio must become AAC — 16 kHz mono, negligible, and the recorder has always done exactly that.
The **video** never needed touching: MSE plays HEVC directly, hardware-decoded by the browser. So
`#video=copy#audio=aac`, and the player is pinned to `stream.html?...&mode=mse` — left to
negotiate, go2rtc's player tries WebRTC first on every load, *necessarily* fails on HEVC, and only
then falls back to MSE, i.e. a guaranteed round of reconnect churn per tile. As a bonus the tile
now shows the camera's native frame instead of a downscaled re-encode.

**Diagnostic dead ends, recorded so they are not re-run.**
- **Memory was never the constraint.** `memory.pressure=0`, `pgscan=pgsteal=0`, `oom_kill=0` — the
  §33 caps caused no reclaim. (The go2rtc cap was still raised 512m→1g: 512m was sized off an
  **idle** 8 MB reading, and the real driver is the per-viewer transcode, so it was thin headroom,
  not a live fault.)
- **Not the recorder.** Zero respawns; both recorder ffmpegs stayed up across the whole session.
- **The cameras' ONVIF advertises H.264 720p/480p** (`GetVideoEncoderConfigurations`) while
  actually emitting **HEVC 1080p + PCMA**. The media config is decorative, like the PTZ zoom decoy
  in §32. Switching the encoder via `SetVideoEncoderConfiguration` is untested — it writes to live
  hardware, so it needs the owner's go-ahead. If it works it is strictly better (H.264 + PCMA are
  both native WebRTC codecs → passthrough with no browser HEVC dependency).

**Caveat.** MSE-HEVC needs browser support: Chrome/Edge on Win11 (and Win10 with the HEVC Video
Extension) and Safari have it, Firefox does not. Verify in the actual browser before assuming.

### §34 addendum — the real culprit was the camera's bitstream, and the swap that fixes it

Removing the transcode exposed something it had been hiding. Measured 30 s per stream, both
through go2rtc and connecting straight to the camera (same result either way, so it is firmware,
not our pipeline):

| stream | resolution | fps delivered | reception errors |
|---|---|---:|---|
| `/onvif1` | 1920×1080 | **8.6** | `PPS id out of range` ×74–85 |
| `/onvif2` | 640×360 | **10.1–13** | **none** |

The camera advertises 15 fps / 2 Mbps and delivers ~9 fps / 0.24 Mbps of **malformed HEVC** on its
main stream. Frames with a broken PPS are dropped at depacketisation, which the viewer sees as
freezing. **The old 1080p re-encode was accidentally acting as a bitstream repair** — that is why
playback looked smoother while burning 27% of a core per camera. The substream is clean.

**`SetVideoEncoderConfiguration` is another decoy.** Tested on .102 with the owner's go-ahead:
returns **200, no fault**, and changes nothing — not the live stream, not even the camera's own
readback. Its media config is a static template that already claims `H264 1280x720` while the
camera emits HEVC 1080p. So the codec cannot be changed over ONVIF; it lives in the Gwell P2P
settings (`T0`/cmd 8), still blocked on the transport RE. Nothing was written to the camera.

**Resulting design — resolution follows tile size**, which is what an NVR does:

| view | stream | source | CPU (measured) | delivers |
|---|---|---|---:|---|
| Grid | `cam_<mac>_web` | substream, **video passthrough** + AAC audio off the main | **5.2% for 2 cams** (~2%/cam) | 640×360 |
| Single | `cam_<mac>_hd` | main feed **re-encoded** to H.264 | **25.8%** | 1920×1080 |
| Recording | `cam_<mac>` | main feed, `-c:v copy` | ~0% | 1920×1080 |

The expensive variant is the deliberate exception: single view shows **one** camera, and go2rtc
keeps an `ffmpeg:` source idle until something consumes it, so grid view pays nothing for it.
`Camera.substream_url` derives the second feed from the probed `capabilities['stream_paths']`;
a camera advertising one path gets no `_hd` and stays on passthrough. The frontend swaps a tile's
source only when it actually changes (`frame.dataset.src`), so ordinary re-renders still never
reload a stream.

### §34 addendum 2 — why HEVC-to-browser could not work, and the final shape

Passing HEVC through was tried and abandoned. Two concrete defects in what the browser actually
received, both measured on go2rtc's fMP4:

1. **Wrong track header.** It declared `2560x1440` for frames that decode as `640x360` (go2rtc's
   HEVC SPS parse). A decoder configured for the wrong size against that bitstream stalls.
2. **Jittering sample durations** — 90/120/91/112 ms, ±30%, because the cameras send **no PTS**
   (the same defect §33 fixed in the recorder) so go2rtc times samples by arrival.

Re-encoding to H.264 fixes both at once: header `640x360` matching the frames, and a flat
`0.100000 s` per sample. Verified side by side.

This matches what shipping NVRs do. Frigate's camera-setup docs open with *"cameras configured to
output H.264 video and AAC audio will offer the most compatibility"* and *"use the substream for
live viewing, keep the main H.265 stream for recording"*. The only reason those setups look free
is that **their cameras are set to H.264 at the source**; ours cannot be (`SetVideoEncoderConfiguration`
is a decoy). So the re-encode is not avoidable here — it is only made cheap, by running it on the
substream.

**Final shape** (measured, 2 cameras):

| view | stream | source | CPU | delivers |
|---|---|---|---:|---|
| Grid | `_web` | substream → H.264, audio merged from main | **16.5% for 2** (~8%/cam) | 640×360 |
| Single | `_hd` | main → H.264 | **33.6%** | 1920×1080 |
| Recording | base | main, `-c:v copy` | ~0% | 1920×1080 |

Against the 122% the old always-1080p-transcode cost for the same two cameras, grid is **~7×**
cheaper. Audio is encoded as **both** AAC and Opus (free at 16 kHz mono) so the player negotiates
**WebRTC** — lowest latency, and universal now that the video is H.264, which drops the browser
HEVC dependency entirely. The forced `mode=mse` added earlier is gone with it.

**Known ceiling:** grid is 640×360 because that is the only substream these cameras publish, and
their encoder config cannot be changed. Single view is the full 1080p. A camera whose substream is
configurable (most brands) would give a sharper grid with no code change — `substream_url` just
uses whatever the probe found.

### §34 addendum 3 — the picture ceiling is the camera, and the CPU ceiling is the host

Live view is stable now, but the grid looks poor. Measured why, so the trade is on the record:

- **The substream is 640x360 at 37 kbps** straight from the camera. Our re-encode of it emits
  306 kbps — eight times more bits than the source carries. **No encoder setting recovers detail
  the camera never sent**; the grid's picture ceiling is the camera's substream, not our pipeline.
  The main feed re-encodes to ~3000 kbps, which is the visible difference.
- **Fixed frame rate is mandatory, and it is also free money.** The transcodes were producing
  20 fps from a source that delivers ~10 real fps — pure duplicated-frame encoding. Pinning the
  stream to 10 fps halved CPU for two 1080p streams, **121% -> 60%**, at an identical bitrate.
  This is now imposed with an `fps=10` filter, not output `-r 10`: the filter preserves regular
  timestamps without continuously manufacturing new output timestamps after decoded input stops.
  Raw `-fps_mode passthrough` is cheaper still (53%) but **reintroduces the 67/133 ms jitter** and
  with it the freezing. Set `live_fps` to what a camera actually delivers, not what it advertises.
- **Full-resolution grid does not fit this host.** With the recorder and both audio tracks live,
  two cameras on `_hd` put the go2rtc cgroup at `cpu.pressure full avg10 = 29.7` — the freezing
  regime (29.6). On the substream the same load sits at ~2. So `grid_hd_max_cameras` ships at
  **0**; it is a knob, not a default. **More vCPU for the WSL is the single change that unlocks
  grid quality** — the box has 2 of a 32 GB machine's cores.

**PTZ is not ours.** Measured end to end: `ptz.start()` (what the D-pad actually calls) is
**2.3 ms** median, and the full HTTP round trip through FastAPI + auth is **5-14 ms**. Our side
contributes nothing. What remains is firmware: the camera's ~0.4 s fixed, uninterruptible step
that also ignores Stop (§13), so panning is discontinuous by construction. Only the Gwell P2P
path — what the vendor app uses, and what feels faster — can change that. (Note `ptz.move()`, the
one-shot `step` action, does block 0.5 s on `PULSE_SECONDS`; the D-pad never calls it.)

### §34 addendum 4 — permanent frozen-frame loop and ephemeral decoder recovery (2026-08-10)

Both HD tiles eventually froze while their recording segments continued changing normally. At the
same moment each browser-facing FFmpeg rose from its usual 14–20% CPU to 56–87%. A new decoder on
the shared HEVC restream reproduced the cause on both cameras:

- `PPS id out of range: 0`;
- `Could not find ref with POC ...` / `Error constructing the frame RPS`;
- no usable decoded picture, while output `-r 10` kept recoding the last good one.

That last behaviour defeated all three watchdog signals: H.264 packets, browser
`framesDecoded`, and media time continued advancing even though the image content did not. There
was also an independent option-order bug: `#raw=-g 20` is expanded before go2rtc's built-in H.264
template, which then appended `-g 50`. `ffprobe` confirmed the effective keyframe interval was five
seconds, not two.

**First fix, superseded.** A dedicated lazy `_live` source made recovery fresh, but meant every
camera served two simultaneous RTSP sessions (recording plus live) and every browser rebuild paid
the slow source/IDR startup again. That explains the recurring loading state and unnecessarily
loads the camera's small hardware.

**Current fix.** Each camera now has exactly one native base producer. Recording copies it and one
preloaded `_hd` FFmpeg consumer converts it to H.264 once on the server. Every WebRTC/MSE consumer
fans out from that hot local producer; the `_web` 640×360 choice is downscaled locally from `_hd`
rather than opening `/onvif2`. After deployment, `ss` showed exactly two established connections
to port 554 for two cameras, while HD packet counters advanced continuously and both recordings
continued to grow. An explicit SD `ffprobe` returned H.264 640×360 + AAC without adding a third
camera connection.

The two cameras also proved that an `fps=10` filter alone is insufficient when the input timeline
itself runs fast: the quintal stream declared 10 fps but emitted **13.1 Mbps in wall time** against
a 4.5 Mbps VBV cap and pushed FFmpeg above 100% CPU; the garage stream respected 4.5 Mbps. The HD
source now uses go2rtc's `#async` input mode, which prepends FFmpeg wall-clock timestamps and audio
sync. Frame pacing, bitrate control and stall timing are therefore based on server time rather than
the camera's broken RTP clock.

Recovery now distinguishes failure domains. A stalled browser decoder gets a fresh PeerConnection
only. If the server H.264 packet counter also stalls, the client disposes its consumer and calls
`POST /api/media/recover/{mac}`; the server cycles only that stream's preload/FFmpeg process. The
base camera producer and recorder remain connected throughout.

The generated software H.264 template is now the final owner of `fps`, GOP, fixed keyint and
scene-cut settings; per-stream `#raw` contains only bitrate limits. The first complete IDR was
measured just under 10 seconds, but it is now paid once during server startup rather than after
every browser reload. Steady-state stalls still recover on the normal threshold, while go2rtc's
exec producer and the dashboard each get 45 seconds to start so neither recycles a valid slow
launch before frame one.
The template must begin with `-c:v`: go2rtc's AAC+Opus multimode mapper recognizes video codecs by
that prefix and prepends `-map 0:v:0?`; placing `-vf` first silently created an audio-only producer.

### §34 addendum 5 — live means discard backlog, not replay it faster (2026-08-11)

A garage-camera incident looked frozen at one burnt-in timestamp, survived both F5 and the tile's
restart button, and then appeared to run faster until current time. The correlated client events
made the failure domain unambiguous: transport was the MSE fallback, the server video-packet counter
advanced continuously, `bufferedGap` reached exactly five seconds, and the player repeatedly entered
`waiting` at `playbackRate=1.25`. That speed was explicitly set by our MSE catch-up controller.

The five-second retention window had accidentally become the playback target. It also pruned a small
slice on nearly every fragment, adding avoidable `SourceBuffer` work. MSE now keeps playback at 1×
and, when more than 1.5 seconds behind, seeks directly to 250 ms from the live edge. History pruning
uses hysteresis (prune after 12 seconds, retain eight). A `live_edge_jump` diagnostic records how much
stale media was discarded.

The restart button had only replaced the browser element, so F5 and the button both reattached to the
same preloaded FFmpeg producer and could inherit its backlog. It now means “return to live”: dispose
the browser consumer, cycle only that camera's local `_hd` preload/FFmpeg, and reconnect. The shared
base RTSP producer and recording remain uninterrupted; view and quality changes still use the cheap
browser-only replacement.

### §34 addendum 6 — intercom can corrupt the RTSP audio clock and delay video (2026-08-11)

A bounded host-only two-way-audio test on the garage camera left the recordings and native HEVC
restream current, but the shared browser-facing `_hd` transcode fell almost one minute behind. A
frame captured from the base stream showed `16:53:13`; the same camera's HD producer was still at
`16:51:59`. The yard HD producer remained current. This isolated the queue to the local garage
FFmpeg rather than the camera, LAN, browser or recorder.

Reproducing the complete video + AAC + Opus pipeline exposed the trigger immediately after the
intercom session: FFmpeg repeatedly reported `Queue input is backward in time` and non-monotonic
audio DTS. RTSP output interleaves all tracks, so a regressing camera-audio clock held back valid
video packets even though video decode/encode itself ran faster than real time. Packet counters and
browser frame counters could not detect this class of failure because delayed pictures continued
to advance.

Every audio-capable live transcode now includes
`-af aresample=async=1:first_pts=0` in its single go2rtc `#raw` block, before AAC and Opus encoding.
The same clock repair was already proven in the recorder. In a controlled reproduction it removed
all backward-time/DTS errors and sustained `1.16x` processing speed. After deployment, garage HD
converged from nine seconds of cold-start lag to the current wall clock and stayed there while the
base recordings continued. Silent cameras do not receive an unnecessary audio filter.

## 35. Recording namespace is UTC, independent of camera/host timezone (2026-08-11)

The segment muxer expands its `%Y/%H` output template in the FFmpeg process timezone. The container
currently happened to be UTC, but this was not an invariant for host execution or a deployment that
sets `TZ`; camera time synchronization is also not a trustworthy persistence clock. Recorder FFmpeg
children now run with `TZ=UTC0`, directory pre-creation uses `datetime.now(UTC)`, indexed timestamps
carry `+00:00`, and retention uses a UTC cutoff. Compose declares `TZ=UTC` too, but the child-level
pin is the actual guarantee. Historical paths are left untouched because their source timezone
cannot be inferred safely. See ADR `docs/internal/0016-utc-recording-layout.md`.
