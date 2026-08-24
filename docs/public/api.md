# Community Cam Guard — API reference

REST API for discovering, streaming, recording and controlling ONVIF/RTSP cameras. Build your own
UI (or scripts) against these endpoints — the bundled dashboard is just one consumer of this API.

- **Base URL:** `http://<host>:3200` (LAN by default; see `HOST`/`PORT` in `.env`).
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

### Factory provisioning (authenticated trusted LAN only)

These endpoints are intentionally distinct from adding a camera that is already on the LAN. They
require authentication and a direct trusted-LAN request using a literal loopback, RFC1918 or IPv6
ULA/link-local address. The Origin/Referer must match Host; public clients, DNS rebinding names,
cross-site requests and any public forwarded hop receive **403** even with a valid session. The BLE
subset may additionally use an explicitly enabled, same-origin HTTPS tunnel.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/provisioning/status` | — | Reports available onboarding stages/transports. |
| GET | `/api/provisioning/vendor-account/status` | — | Trusted-LAN only. Reports whether the encrypted renewable vendor session is configured; never returns the account identity or tokens. |
| POST | `/api/provisioning/vendor-account/login` | `{account_type, account, password, mobile_area?, language?, region?, area?}` | Trusted-LAN only. Performs native account login and stores credentials/session encrypted. Android, Frida and capture files are not involved. |
| POST | `/api/provisioning/vendor-account/refresh` | — | Trusted-LAN only. Renews the encrypted native session without returning credentials or token material. |
| POST | `/api/provisioning/inspect` | `ProvisioningLabelIn` | Validates the scanned label or manual identity without contacting the camera. |
| GET | `/api/provisioning/networks` | — | Read-only Wi-Fi scan. Returns display names, short-lived signed selection IDs and whether manual fallback is allowed. |
| POST | `/api/provisioning/networks/manual` | `{ssid, security}` | Creates a signed selection for an explicit SSID, but only when the server has no usable Wi-Fi scanner. |
| POST | `/api/provisioning/start` | `ProvisioningStartIn` | Generates the recovered vendor Wi-Fi QR in memory for labels that advertise QR setup. SoftAP-only devices still fail closed with `501`. |
| POST | `/api/provisioning/ble/prepare` | `ProvisioningStartIn` | Renews the native account session, obtains fresh TanKey/bind-token material and prepares encrypted BLE Wi-Fi stages; secrets remain server-side. An owner-only research file is retained only as a compatibility fallback. |
| POST | `/api/provisioning/ble/decode-response` | `ProvisioningBleResponseIn` | Decodes one GATT response. Discards the credential-bearing `0x83` echo and retains a valid `0x85` handoff only in bounded process memory. Never binds automatically. |
| POST | `/api/provisioning/privileged/online-status` | `ProvisioningOnlineStatusIn` | Read-only APK-compatible lookup of the current `configToken`. A successful `status == 1` creates the alternative no-`confirmKey` handoff. |
| POST | `/api/provisioning/privileged/status` | `ProvisioningLabelIn` | Reports whether a fresh post-Wi-Fi P2P handoff is pending; returns no proof/token. |
| POST | `/api/provisioning/privileged/bind` | `ProvisioningPrivilegedBindIn` | Explicit stage 2: bind the camera to the captured IoTVideo account. It still does not enable RTSP. |
| POST | `/api/provisioning/privileged/p2p-probe` | `ProvisioningLabelIn` | Uses encrypted post-bind material to authenticate to the P2P access node, inspect aggregate account/target visibility, heartbeat and resolve the selected target's TermDNS route. It does not CALL or send any command to the camera. |
| POST | `/api/provisioning/privileged/p2p-route-probe` | `ProvisioningLabelIn` | Performs a bounded brokered CALLING and direct CA/CB NAT handshake for the selected camera. It exposes no peer address/session secret and opens neither media nor a control channel. |
| POST | `/api/provisioning/privileged/p2p-property-read` | `ProvisioningP2PPropertyReadIn` | Trusted-LAN only. Opens the selected camera route and sends one allowlisted B7 thing-model read. Unknown paths are rejected before network I/O. There is no arbitrary write/action API. |

`ProvisioningLabelIn` accepts `label` (for example the complete printed QR URL), `device_id`,
`capability_code`, `firmware_version` and `mac`. A scanned label may supply the ID and capability
code by itself. `ProvisioningStartIn` adds the selected `wifi_network_id` returned by the scan,
`wifi_password` and optional `name`. Arbitrary SSID text is not accepted. The signed network choice
expires after five minutes. The Wi-Fi password is request-local and is never persisted, logged or
returned as plain text. The QR response is marked `Cache-Control: no-store`; its SVG necessarily
encodes the selected SSID and password and the browser revokes its temporary object URL when the
dialog closes.

The account endpoints remove the Android application, emulator, Frida and captured-session files
from the production onboarding path. They do **not** make provisioning LAN-only: login, TanKey and
bind-token requests still use the vendor cloud. Account identity, uppercase password-equivalent
digest, renewable token and provisioning IDs are stored together inside a Fernet-encrypted SQLite
payload. Only safe booleans cross back to the browser. Configure the account from a direct trusted
LAN client before starting Web Bluetooth through a temporary HTTPS tunnel; the tunnel exception
does not apply to account enrollment.

The start response is intentionally `status: "awaiting_camera_scan"`, `experimental: true` and
`cloud_token_used: false`. It means only that an artifact matching the APK's recovered modern QR
format was produced. The user must put the camera in QR pairing mode and wait for its physical
acknowledgement; the API does not claim that the camera read, accepted or joined the network.
The renderer matches the current APK's high (`H`) QR error-correction level. The vendor flow also
obtains a per-setup `configToken`; the LAN-only experimental response currently identifies
`cloud_token_used: false`, so firmware acceptance is not yet guaranteed even when the code is
optically decoded.

For labels that advertise Bluetooth, the modal selects only the exact `GW_BLE_<deviceId>` device,
negotiates the recovered secure session, reads the camera's Wi-Fi scan, sends the selected network
and waits for the camera to confirm its Wi-Fi association. This is physically validated; see
[Bluetooth onboarding](bluetooth-onboarding.md). The browser device needs Bluetooth and access to
the dashboard, and Web Bluetooth requires a secure context (`localhost` or trusted HTTPS).

After Wi-Fi confirmation the modal exposes two explicit choices. **Finish Wi-Fi only** sends the
Bluetooth finish command without binding anything. **Link P2P access** consumes the short-lived
handoff through `/provisioning/privileged/bind`; receiving `0x83` alone never contacts the vendor
binding service. The handoff and returned subscription token stay in memory, expire with the setup
session and are never returned. A successful bind reports RTSP as `pending`: the separately
homologated post-bind sequence still has to initialize P2P, enable `onvifEn`, install the camera's
HA1 password value, verify real RTSP media and insert the encrypted clear credential in the
registry. See
[Bluetooth onboarding](bluetooth-onboarding.md#post-wi-fi-p2p-and-rtsp-stage) for the exact contract.
The successful bind persists the 64-byte terminal access token and 128-hex device subscription
token together as one Fernet-encrypted SQLite value. They never cross back into the browser.
`p2p-probe` validates node certification, account inventory, heartbeat and optional target TermDNS
after a restart while deliberately stopping before direct camera contact. The explicit
`p2p-route-probe` continues through A4/A3 rendezvous and CA/CB to prove that the selected camera is
reachable. It returns only booleans/counts: the broker/camera address, link ID, call ID, cookie,
session key and credentials never cross the API boundary.

`p2p-property-read` accepts the same camera identity plus `property_path`. The path must be one of
the capability roots recovered from the APK and compiled into the backend allowlist. Even
`Action.*` roots are queried with B7 and are never executed with AC. The response contains the
camera-owned JSON value, transport acknowledgement and device error code. Unlike the temporary
Web-Bluetooth subset, this route never accepts the remote HTTPS-tunnel exception.

The driver also contains one internal typed D2 operation for image orientation. It accepts only
normal/180°, uses a fixed property path and requires a successful B7 preflight, correlated D3 and
fresh B7 readback. It is not exposed as an HTTP endpoint yet; no arbitrary property writer or action
endpoint exists.

When the server has no Wi-Fi radio/scanner, `GET /provisioning/networks` returns
`manual_entry_allowed: true`. The localhost UI then accepts an explicit 1–32-byte SSID and
`wpa`, `wep` or `open` security. The manual endpoint refuses requests with `409` while automatic
scanning is available, so this remains a capability-based fallback rather than the normal path.

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
  "event": "live_edge_jump",
  "mac": "aa:bb:cc:dd:ee:ff",
  "stream": "cam_aabbccddeeff_hd",
  "metrics": {"transport": "mse", "bufferedGap": 2.4, "discardedSeconds": 2.15}
}
```

Allowed events are `waiting`, `stalled`, `playing`, `live_edge_jump`, `catchup_start`, `catchup_end`,
`mse_failure`, and `watchdog_recovery`. The catch-up pair remains accepted for older cached clients;
current clients discard stale live media instead of accelerating it. `GET /api/media/client-events`
returns the last 200 events from the current server process, oldest first. Each event includes a UTC
timestamp and, when available, a snapshot of the matching go2rtc stream packet/consumer counters.

### Storage

| Method | Path | Notes |
|---|---|---|
| GET | `/api/storage` | Disk usage and recording policy state (alert/full/resume marks). |

### Recordings

| Method | Path | Params | Notes |
|---|---|---|---|
| GET | `/api/recordings` | `mac`, `day_from`, `day_to`, `limit`, `offset` (all optional) | Paginated segment index (newest first). Dates and `started_at` are UTC. Includes `total` and `retention_days`. |
| GET | `/api/recordings/file` | `path` (required) | Play a recorded `.mp4`. A first HEVC view is transcoded progressively while a seekable H.264 cache is built; cache hits and browser-native codecs are served directly. |
| GET | `/api/recordings/playback-status` | `path` (required) | Reports `{ready, cached, transcoding}` so a first progressive view can switch to the completed seekable cache. |
| GET | `/api/recordings/download` | `path` (required) | Download the original `.mp4` with `attachment` disposition and a server-generated `Camera_UTC-timestamp.mp4` filename. |

```bash
curl -b jar.txt "http://127.0.0.1:3200/api/recordings?mac=aa:bb:cc:dd:ee:ff&limit=50"
```

### Health

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | none | `{"status":"ok"}` — liveness probe, no `/api` prefix, no auth. |
| GET | `/api/build` | none | Content-derived application build ID used for automatic dashboard cache busting. |

---

## Errors

Standard HTTP status codes with a JSON `{"detail": "..."}` body:

| Code | Meaning |
|---|---|
| 401 | Not authenticated (log in first). |
| 403 | Factory provisioning did not originate from the authenticated trusted local network. |
| 422 | Validation error — including a **wrong camera password** on add (deliberately not 401, so a UI doesn't bounce to login). |
| 404 | Camera not found. |
| 501 | Camera/driver doesn't support the requested action (e.g. PTZ/reboot). |

> This reference is hand-maintained; the authoritative, always-current schema is
> [`/api/openapi.json`](/api/openapi.json). If they disagree, trust the schema and please open an issue.
