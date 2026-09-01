# Community Cam Guard (CCG)

**Open-source, self-hosted NVR and dashboard for generic ONVIF / RTSP IP cameras** — a
privacy-friendly replacement for the limited, cloud-locked vendor apps that ship with cheap
Chinese Wi-Fi cameras (Yoosee, XiongMai/XMEye, Dahua/Hikvision clones, and more). Discover
cameras on your LAN, watch them live, record 24/7, and control them — all local, no cloud.

<sub>open-source NVR · ONVIF · RTSP · Yoosee · self-hosted camera dashboard · WebRTC · go2rtc · home surveillance</sub>

> Status: working end-to-end — no-auth discovery + opaque driver-independent camera identity,
> credential-validated add with automatic capability probe, live streaming (go2rtc/WebRTC) with
> listen-in audio, **PTZ** control (press-and-hold), 24/7 crash-safe recording with browser
> playback + time-based retention, storage policy, a localized (en/pt-BR) web dashboard with a
> dedicated **Cameras** setup tab, and a REST API. Recorded voice messages and bounded push-to-talk
> are implemented through the Yoosee driver's legacy/IoTVideo-specific transports; final physical
> speech validation of the corrected IoTVideo path remains (see `docs/DECISIONS.md`).

## Documentation

| | |
|---|---|
| **[Setup](#setup)** | Run it — Docker on Linux/macOS/Windows, or Python directly. |
| **[Bluetooth onboarding](docs/public/bluetooth-onboarding.md)** | Put a factory-new camera on Wi-Fi without the vendor UI. |
| **[API reference](docs/public/api.md)** | Every REST endpoint — build your own UI/scripts. Interactive at `/api/docs`. |
| **[Contributing](CONTRIBUTING.md)** | Dev setup, standards (ruff/mypy/pytest), how to add a camera brand. |
| **[Roadmap](ROADMAP.md)** | Backlog, priorities, milestones. |
| **[Design decisions](docs/DECISIONS.md)** | The *why* behind the architecture. |

## Supported cameras & adding your own

Camera-family knowledge lives in a **pluggable driver layer** (`backend/app/drivers/`). A simple
RTSP family contributes discovery paths and detection through one registered module; a proprietary
family owns a vertical package with model profiles, provisioning and typed control adapters. The
API dispatches semantic controls through the selected driver and never accepts raw vendor commands.
Verified: **Yoosee / generic HiSilicon** (ONVIF PTZ plus typed P2P controls). Shipped discovery
drivers: XiongMai / XMEye, Dahua- and Hikvision-style, plus a generic fallback. See
`CONTRIBUTING.md`; contributions for specific models are very welcome.

## Why it exists

Cheap IP cameras usually speak **ONVIF + RTSP** on the LAN, but their official apps lock you
into the cloud and expose few controls. This project talks to the cameras directly on your
network and gives you one dashboard with live view, 24/7 recording, and (per-camera, when
supported) PTZ, audio, microphone and LED/siren control.

## Feature detection is per-camera

Cameras vary wildly. On discovery we probe each device (ONVIF capabilities + fallback
scans) and **only enable in the UI the features the camera actually supports** — PTZ, audio
out, two-way audio (mic), digital outputs (LED / "anti-thief" siren).

## Architecture

```
                 ┌────────────────────────── FastAPI (Python) ──────────────────────────┐
   Browser ──────┤  auth (secret-key cookie) · REST API · discovery · recording manager │
   (dashboard)   └───────────────┬───────────────────────────────────┬──────────────────┘
        │  WebRTC/HLS            │ manages                           │ spawns
        ▼                        ▼                                   ▼
   ┌──────────┐          ┌──────────────┐                    ┌──────────────────┐
   │  go2rtc  │◀── RTSP ─┤   Cameras    │                    │ ffmpeg -c copy   │
   │ (media)  │   ONVIF  │  (LAN, WiFi) │                    │ segment recorder │
   └──────────┘          └──────────────┘                    └────────┬─────────┘
                                                                      ▼
                                                    recordings/<cam>/<UTC-date>/<UTC-hour>/*.mp4
                                                    + SQLite index  → (optional) S3 tiering
```

- **Discovery** — active port/path scan (the reliable path for these cams; WS-Discovery is kept
  but the units ignore multicast) that **identifies each camera credential-free** via the no-auth
  ONVIF device + media services (vendor/model/firmware, real RTSP paths, and the MAC from
  `GetNetworkInterfaces`). Stdlib-only, no fragile ONVIF wheels.
- **Media engine** — [go2rtc](https://github.com/AlexxIT/go2rtc): RTSP → WebRTC (low latency) / HLS.
  Runs as its own container (compose) or a single static binary; the app generates its config from
  the registry and reloads it on changes.
- **Recording** — `ffmpeg` segment muxer in `-c:v copy` (remux, no re-encode → near-zero CPU),
  configurable chunk length (`SEGMENT_SECONDS`, default 300) laid out by UTC date/hour and indexed in
  SQLite. Each segment is a **crash-safe fragmented MP4** (playable up to its last flushed fragment
  after a hard kill). Streams are HEVC; the recordings player transcodes HEVC→H.264 on demand into a
  size-capped LRU cache so clips play (and seek) natively in the browser.
- **Storage** — local-first; optional S3 tiering via `.env`. A disk monitor alerts at 80% and
  **skips saving (keeps streaming) when full**, resuming automatically when space frees, and
  **never deletes**. A separate opt-in **retention** job (`RECORDING_RETENTION_DAYS`) trims footage
  older than N days (0 = keep forever).
- **Auth** — a secret key from `.env` gates the dashboard via a signed session cookie.

## Platforms & networking

Runs anywhere Docker does — **Linux, macOS and Windows**. The one requirement is that the host can
reach the cameras on your LAN (discovery and RTSP are direct, on-network):

- **Linux / macOS** — host networking works out of the box; nothing special to do.
- **Windows** — run via **Docker Desktop** or **WSL2**. WSL2's default NAT can't reach LAN cameras
  and blocks multicast discovery, so enable **mirrored** networking. Add to `C:\Users\<you>\.wslconfig`:

  ```ini
  [wsl2]
  networkingMode=mirrored
  ```

  then `wsl --shutdown` and reopen (fully reversible). Outbound setups like `cloudflared` tunnels and
  `nginx` via `localhost` keep working; only setups hard-coding the old `172.x` WSL IP need care.

## Setup

```bash
cp .env.example .env          # set DASHBOARD_SECRET_KEY at minimum

# Recommended: the whole stack (app + go2rtc) via Docker Compose, host networking:
docker compose up -d --build
# then open http://<server-lan-ip>:3200 (or localhost on the server) and unlock with
# DASHBOARD_SECRET_KEY.
# The frontend is bind-mounted (live edits); rebuild the app image after backend changes.
```

Or run it directly with Python (spawns/owns the go2rtc binary itself):

```bash
pip install -e .              # or: pip install -e '.[s3,dev]'
python -m backend.app.discovery.active_scan       # optional: gentle subnet scan from the CLI
python -m backend.app.main                         # API + dashboard + go2rtc + recorder + storage
```

Adding cameras is done in the dashboard's **Cameras** tab: hit **Scan network**, then attach a
username/password to each discovered camera and **Add** — the credentials are verified and the
device's capabilities probed on the spot, so the controls light up immediately.

The same tab has a **Set up new camera** button for factory onboarding. The authenticated dashboard
is reachable from the local network, but provisioning additionally rejects public, cross-site and
publicly forwarded requests. Bluetooth onboarding is physically validated on the current Yoosee
firmware; see the [step-by-step guide](docs/public/bluetooth-onboarding.md). The browser device must
have Bluetooth and network access to the dashboard. Web Bluetooth also requires a secure browser
context: use `localhost` on a Bluetooth-capable server, trusted local HTTPS, or a temporary HTTPS
tunnel. Plain `http://192.168.x.x:3200` is suitable for normal dashboard use but not Web Bluetooth.

The authenticated app listens on the LAN at **3200**. Internal go2rtc services remain loopback-only:
API **3201**, WebRTC **3202**, RTSP restream **3203**. Only port 3200 should be opened in the host
firewall.

> **Set `DASHBOARD_SECRET_KEY` before adding cameras and don't change it afterwards.** Camera
> passwords are encrypted at rest with a key derived from that secret, so changing it makes
> stored passwords undecryptable — streams then fail to authenticate and you must re-enter each
> password. (The app logs a warning when this happens.)

Need the media engine binary? `go2rtc` is expected at `./bin/go2rtc` (Linux amd64 build from
the [go2rtc releases](https://github.com/AlexxIT/go2rtc/releases)).

### Tests

```bash
pip install -e '.[dev]'
pytest                    # the suite in tests/
ruff check backend tests  # lint
mypy backend/app          # type-check
```
The suite (300+ tests, ~91% coverage) covers the logic — camera drivers, RTSP parsing/auth + credential
verification, encryption, capability probe, PTZ/reboot control, storage policy, retention + playback
cache, recordings pagination, the REST endpoints and go2rtc config — against a throwaway DB, no
cameras or network needed. Lint and types are enforced in CI (`.github/workflows/ci.yml`).

## Operating note: power-cycle the cameras first

> **Always reboot (power-cycle) your cameras before a fresh discovery/streaming session, to
> restore a known-good initial state.**

The cheap generic Wi-Fi cameras this project targets run tiny embedded RTSP servers that can
**hang or drop off the network under connection pressure** (rapid scans, many simultaneous
clients, an app that reconnects aggressively). When a camera is in this degraded state you'll
see symptoms like RTSP `SETUP` returning `400 Bad Request`, `i/o timeout` on port 554, or the
camera not even answering `ping` — none of which mean your path, credentials or network are
wrong. A power-cycle (unplug ~10s, plug back in, wait ~30–60s to rejoin Wi-Fi) clears it.

Because of this, discovery and probing are deliberately **gentle** (low concurrency, one reused
connection per camera, prompt teardown). Even so, rebooting the cameras at the start of a
session is the reliable way to guarantee a valid baseline.

Tip: clients see a stable opaque camera ID. The current ONVIF/RTSP driver re-matches its private
MAC identity after DHCP changes; future drivers may map a serial or vendor-native ID instead.

## Roadmap

Discovery, driver-independent camera IDs, go2rtc live view, 24/7 recording + retention, storage policy,
pluggable drivers, capability probe, PTZ + listen-in audio, the recordings browser and the REST
API are all **working**. Still open: final physical validation of continuous **two-way audio
API are all **working**. Yoosee two-way audio is physically homologated on every current unit through
the recovered LAN RTSP backchannel, including recorded messages and hold-to-speak from the browser.
Still open: WAN-independent camera bootstrap/control, long-session intercom hardening, broader
camera-family coverage for proprietary controls, and optional S3 tiering. See `ROADMAP.md` and
`docs/internal/0008-reboot-and-two-way-audio-live-in-vendor-p2p.md`.

Full backlog, priorities and milestones: **[ROADMAP.md](ROADMAP.md)**.

## License

MIT.
