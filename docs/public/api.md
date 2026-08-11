# Community Cam Guard — API reference

REST API for discovering, streaming, recording and controlling ONVIF/RTSP cameras. Build your own
UI (or scripts) against these endpoints — the bundled dashboard is just one consumer of this API.

- **Base URL:** `http://<host>:3200` (loopback by default; see `HOST`/`PORT` in `.env`).
- **Interactive docs:** Swagger at [`/api/docs`](/api/docs), ReDoc at [`/api/redoc`](/api/redoc),
  raw schema at [`/api/openapi.json`](/api/openapi.json).
- **Content type:** JSON request/response unless noted.

## Authentication

Auth is a **session cookie**, not a token header. Log in once with the dashboard key; the server
sets an HTTP-only cookie `ccg_session` that every other endpoint requires.

```bash
# 1. log in — stores the cookie in a jar
curl -c jar.txt -X POST http://127.0.0.1:3200/api/login \
     -H 'Content-Type: application/json' -d '{"key":"YOUR_DASHBOARD_KEY"}'

# 2. use the cookie on every subsequent call
curl -b jar.txt http://127.0.0.1:3200/api/cameras
```

A missing/invalid session returns **401**. The dashboard key is `DASHBOARD_SECRET_KEY` in `.env`.

---

## Endpoints

### Auth

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/login` | `{"key": "..."}` | Sets `ccg_session` cookie. `401` if the key is wrong. |
| POST | `/api/logout` | — | Clears the cookie. |
| GET | `/api/me` | — | `{"authenticated": true}` when the session is valid. |

### Cameras

| Method | Path | Body / Params | Notes |
|---|---|---|---|
| GET | `/api/cameras` | — | List configured cameras (see the camera object below). |
| POST | `/api/cameras` | `CameraIn` | Add **or update** a camera (keyed by MAC). Validates RTSP creds and probes capabilities on add; a wrong password returns **422**. |
| DELETE | `/api/cameras/{mac}` | — | Remove a camera and stop its streams. |
| POST | `/api/cameras/{mac}/probe` | — | Re-probe capabilities (codecs, PTZ, substream…). |
| POST | `/api/cameras/{mac}/ptz` | `PtzIn` | Pan/tilt. See PTZ below. |
| POST | `/api/cameras/{mac}/reboot` | — | Reboot the camera (driver-dependent; `501` if unsupported). |

**`CameraIn`** (fields other than `mac` are optional; omitted fields are left unchanged on update):

```json
{
  "mac": "aa:bb:cc:dd:ee:ff",
  "name": "Garagem",
  "username": "admin",
  "password": "secret",
  "stream_path": "/onvif1",
  "rtsp_port": 554,
  "last_ip": "192.168.1.50",
  "vendor": "yoosee"
}
```

**Camera object** (returned by `GET /api/cameras` and the add/probe responses):

```json
{
  "mac": "aa:bb:cc:dd:ee:ff",
  "name": "Garagem",
  "username": "admin",
  "has_password": true,
  "stream_path": "/onvif1",
  "rtsp_port": 554,
  "last_ip": "192.168.1.50",
  "vendor": "yoosee",
  "capabilities": { "video_codec": "h265", "has_audio": true, "ptz": true, "stream_paths": ["/onvif1", "/onvif2"] },
  "has_audio": true,
  "stream_id": "cam_aabbccddeeff",
  "web_stream_id": "cam_aabbccddeeff_web",
  "hd_stream_id": "cam_aabbccddeeff_hd",
  "has_substream": true,
  "has_quality_variants": true,
  "webrtc_url": "http://127.0.0.1:3201/webrtc.html?src=cam_aabbccddeeff",
  "recording": true
}
```

The password is **never** returned — only `has_password`. Stream IDs are go2rtc stream names (see
Media). `recording` is whether the recorder is currently writing this camera.

**PTZ (`PtzIn`)** — press-and-hold semantics:

```json
{ "direction": "left|right|up|down", "action": "start|stop|step" }
```

- `action:"start"` begins motion in `direction`; `action:"stop"` halts it (direction optional).
- `action:"step"` (default) nudges once. `501` if the camera has no PTZ; `400` for an unknown direction.

```bash
curl -b jar.txt -X POST http://127.0.0.1:3200/api/cameras/aa:bb:cc:dd:ee:ff/ptz \
     -H 'Content-Type: application/json' -d '{"direction":"left","action":"start"}'
```

### Discovery

| Method | Path | Params | Notes |
|---|---|---|---|
| POST | `/api/discovery/scan` | `username`, `password` (query, optional) | Scan the network (ONVIF WS-Discovery + RTSP probing) and return found cameras. Gentle by design — cheap cameras hang under aggressive probing. |

### Media (live streams)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/media/streams` | Media-engine status + live-view settings (below). |
| GET | `/api/media/activity` | Per-stream `{video_packets, consumers}` — liveness for a client freeze watchdog (a watched stream whose video packets stop advancing is frozen upstream). |
| POST | `/api/media/client-event` | Bounded live-player transition snapshot for diagnostics (details below). |
| GET | `/api/media/client-events` | Last 200 browser transition snapshots from this server process. |
| POST | `/api/media/recover/{mac}` | Cycle one camera's local preloaded H.264 producer after a confirmed stall. The shared camera RTSP/recording producer is not restarted. |
| POST | `/api/media/restart` | Regenerate go2rtc config and restart it (after registry changes). |

`GET /api/media/streams` returns:

```json
{
  "go2rtc_api": "http://127.0.0.1:3201",
  "healthy": true,
  "grid_hd_max_cameras": 0,
  "live_quality": "max",
  "quality_levels": ["low", "medium", "high", "max"],
  "live_hwaccel": ""
}
```

To play a stream, point a WebRTC/MSE player at go2rtc using the camera's stream IDs, e.g.
`{go2rtc_api}/stream.html?src={web_stream_id}&mode=webrtc,mse`. `web_stream_id` is the cheap
local 640px derivative; `hd_stream_id` is the shared full-resolution local transcode. Both fan out
from one camera RTSP producer (see `docs/DECISIONS.md §34`).

#### Live-view diagnostics

`POST /api/media/client-event` accepts the dashboard's bounded live-player transition snapshots. It
is authenticated and intended for the bundled player, not as a periodic metrics endpoint. Body:

```json
{
  "event": "catchup_start",
  "mac": "aa:bb:cc:dd:ee:ff",
  "stream": "cam_aabbccddeeff_hd",
  "metrics": {"transport": "mse", "bufferedGap": 2.4, "playbackRate": 1.25}
}
```

Allowed events are `waiting`, `stalled`, `playing`, `catchup_start`, `catchup_end`, `mse_failure`,
and `watchdog_recovery`. `GET /api/media/client-events` returns the last 200 events from the current
server process, oldest first. Each event includes a UTC timestamp and, when available, a snapshot of
the matching go2rtc stream packet/consumer counters.

### Storage

| Method | Path | Notes |
|---|---|---|
| GET | `/api/storage` | Disk usage and recording policy state (alert/full/resume marks). |

### Recordings

| Method | Path | Params | Notes |
|---|---|---|---|
| GET | `/api/recordings` | `mac`, `day_from`, `day_to`, `limit`, `offset` (all optional) | Paginated segment index (newest first). Includes `total` and `retention_days`. |
| GET | `/api/recordings/file` | `path` (required) | Stream a recorded `.mp4` segment (HEVC transcoded to H.264 on demand for the browser). |

```bash
curl -b jar.txt "http://127.0.0.1:3200/api/recordings?mac=aa:bb:cc:dd:ee:ff&limit=50"
```

### Health

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | none | `{"status":"ok"}` — liveness probe, no `/api` prefix, no auth. |

---

## Errors

Standard HTTP status codes with a JSON `{"detail": "..."}` body:

| Code | Meaning |
|---|---|
| 401 | Not authenticated (log in first). |
| 422 | Validation error — including a **wrong camera password** on add (deliberately not 401, so a UI doesn't bounce to login). |
| 404 | Camera not found. |
| 501 | Camera/driver doesn't support the requested action (e.g. PTZ/reboot). |

> This reference is hand-maintained; the authoritative, always-current schema is
> [`/api/openapi.json`](/api/openapi.json). If they disagree, trust the schema and please open an issue.
