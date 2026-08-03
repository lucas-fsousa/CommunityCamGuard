# Community Cam Guard (CCG)

**Open-source, self-hosted NVR and dashboard for generic ONVIF / RTSP IP cameras** — a
privacy-friendly replacement for the limited, cloud-locked vendor apps that ship with cheap
Chinese Wi-Fi cameras (Yoosee, XiongMai/XMEye, Dahua/Hikvision clones, and more). Discover
cameras on your LAN, watch them live, record 24/7, and control them — all local, no cloud.

<sub>open-source NVR · ONVIF · RTSP · Yoosee · self-hosted camera dashboard · WebRTC · go2rtc · home surveillance</sub>

> Status: working end-to-end — no-auth discovery + identification, MAC-keyed camera registry,
> credential-validated add with automatic capability probe, live streaming (go2rtc/WebRTC) with
> listen-in audio, **PTZ** control (press-and-hold), 24/7 crash-safe recording with browser
> playback + time-based retention, storage policy, a localized (en/pt-BR) web dashboard with a
> dedicated **Cameras** setup tab, and a REST API. Two-way audio (talk) and camera reboot are the
> remaining controls (they live in the vendor P2P channel — see `docs/DECISIONS.md`).

## Supported cameras & adding your own

Camera-family knowledge lives in a **pluggable driver layer** (`backend/app/drivers/`). A driver
bundles a family's RTSP paths, detection, capability-probe hooks and controls (PTZ, reboot, …); the
rest of the app is generic and talks only to the driver interface. Verified: **Yoosee / generic
HiSilicon** (full — ONVIF PTZ on port 5000, real RTSP paths via the ONVIF media service). Shipped
discovery drivers: XiongMai / XMEye, Dahua- and Hikvision-style, plus a generic fallback. **Adding a
brand is one file in `drivers/` + one line in the registry** — see `CONTRIBUTING.md`. Contributions
for specific models are very welcome.

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
   Browser ──────┤  auth (secret-key cookie) · REST API · discovery · recording manager  │
   (dashboard)   └───────────────┬───────────────────────────────────┬──────────────────┘
        │  WebRTC/HLS            │ manages                            │ spawns
        ▼                        ▼                                    ▼
   ┌──────────┐          ┌──────────────┐                    ┌──────────────────┐
   │  go2rtc  │◀── RTSP ─┤   Cameras    │                    │ ffmpeg -c copy   │
   │ (media)  │   ONVIF  │  (LAN, WiFi) │                    │ segment recorder │
   └──────────┘          └──────────────┘                    └────────┬─────────┘
                                                                       ▼
                                                    recordings/<cam>/<date>/<hh>/*.mp4
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
  configurable chunk length (`SEGMENT_SECONDS`, default 300) laid out by date/hour and indexed in
  SQLite. Each segment is a **crash-safe fragmented MP4** (playable up to its last flushed fragment
  after a hard kill). Streams are HEVC; the recordings player transcodes HEVC→H.264 on demand into a
  size-capped LRU cache so clips play (and seek) natively in the browser.
- **Storage** — local-first; optional S3 tiering via `.env`. A disk monitor alerts at 80% and
  **skips saving (keeps streaming) when full**, resuming automatically when space frees, and
  **never deletes**. A separate opt-in **retention** job (`RECORDING_RETENTION_DAYS`) trims footage
  older than N days (0 = keep forever).
- **Auth** — a secret key from `.env` gates the dashboard via a signed session cookie.

## WSL networking (important)

WSL2's default NAT networking cannot reach cameras on your LAN, and multicast discovery does
not cross it. On **Windows 11** enable **mirrored** networking so WSL shares the host's
network interfaces. Add to `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then `wsl --shutdown` and reopen. It is fully reversible (remove the line + `wsl --shutdown`).
Outbound-based setups like `cloudflared` tunnels and `nginx` reached via `localhost` keep
working; only setups that hard-code the old `172.x` WSL IP or use custom `iptables` need care.

## Setup

```bash
cp .env.example .env          # set DASHBOARD_SECRET_KEY at minimum

# Recommended: the whole stack (app + go2rtc) via Docker Compose, host networking:
docker compose up -d --build
# then open http://localhost:3200 and unlock with DASHBOARD_SECRET_KEY.
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

Ports are loopback-only in the **32xx** prototype range: app **3200**,
go2rtc API **3201**, WebRTC **3202**, RTSP restream **3203**.

> **Set `DASHBOARD_SECRET_KEY` before adding cameras and don't change it afterwards.** Camera
> passwords are encrypted at rest with a key derived from that secret, so changing it makes
> stored passwords undecryptable — streams then fail to authenticate and you must re-enter each
> password. (The app logs a warning when this happens.)

Need the media engine binary? `go2rtc` is expected at `./bin/go2rtc` (Linux amd64 build from
the [go2rtc releases](https://github.com/AlexxIT/go2rtc/releases)).

### Tests

```bash
pip install -e '.[dev]'   # pytest
pytest                    # runs the suite in tests/
```
The suite (~110 tests) covers the pure logic — camera drivers, RTSP parsing/auth + credential
verification, credential encryption, capability probe, storage policy, retention + playback cache,
recordings pagination, go2rtc config/reload, config, session tokens — against a throwaway DB, no
cameras or network needed.

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

Tip: cameras are tracked by **MAC address**, not IP, so a reboot that changes the DHCP lease
does not lose a camera's identity, name or credentials — it is re-matched automatically.

## Roadmap

- [x] Project scaffold, config, ONVIF WS-Discovery
- [x] Active-scan discovery fallback (MAC identity, curated endpoint whitelist, gentle probe)
- [x] Camera registry (SQLite, MAC-keyed, Fernet-encrypted credentials)
- [x] go2rtc integration (generate config from registry, manage process, WebRTC + RTSP restream)
- [x] 24/7 segment recorder (ffmpeg -c:v copy, 60s MP4 chunks by day/hour) + SQLite index
- [x] Storage monitor (80% alert, skip-save-when-full, auto-resume; never deletes)
- [x] Recording retention — time-based cleanup job (`RECORDING_RETENTION_DAYS`; 0 = keep forever)
- [x] FastAPI backend: secret-key cookie auth + REST API (cameras, discovery, storage, recordings) + service lifespan
- [x] Web dashboard (plain HTML/JS): login, live grid/single via go2rtc, storage banner, recording playback
- [x] Pluggable **camera drivers** (`backend/app/drivers/`) — adding a brand is one file + one line
- [x] No-auth scan **identification** (vendor/model/firmware/RTSP paths/driver) + MAC from ONVIF
- [x] Dedicated **Cameras** tab — scan, filter (all/available/configured), credential-validated add
      with **automatic capability probe** so controls enable on add
- [x] **Recordings browser** — filter by camera + date range, paginated (`limit`/`offset`, capped),
      crash-safe fragmented-MP4 segments, HEVC→H.264 browser playback
- [x] Capability probe (PTZ / audio / video codecs / port roles) + real RTSP stream URIs via ONVIF
- [x] Device control: **PTZ** (ONVIF, press-and-hold) + **listen-in audio** (WebRTC)
- [x] i18n — dashboard localised (en default + pt-BR), browser-detected + header selector; more locales are one dict block
- [x] Playback cache eviction (LRU size cap via `PLAYBACK_CACHE_MB`) + opt-in background pre-transcode (`PLAYBACK_PRETRANSCODE`)
- [ ] Two-way audio (talk/mic) + camera reboot — live in the vendor **Gwell P2P** channel (needs a
      packet capture of the vendor app to reverse; ONVIF is exhausted for these units)
- [ ] Optional S3 tiering

## License

MIT.
