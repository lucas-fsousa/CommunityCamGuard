# 0017 — Factory provisioning is a localhost-only security boundary

**Status:** accepted · **Date:** 2026-08-11

## Context

Adding an existing LAN camera only stores RTSP/ONVIF credentials. Factory provisioning is more
powerful: it accepts a household Wi-Fi password and can change nearby hardware. The authenticated
dashboard may be deliberately published through a reverse proxy, so dashboard authentication and
the socket peer alone are insufficient. A proxy on the same host makes every remote peer appear to
the application as loopback.

Browsers also cannot send arbitrary UDP datagrams to a camera SoftAP. The eventual QR, SoftAP or
BLE transport therefore belongs behind a server API rather than in ordinary frontend JavaScript.

## Decision

Factory onboarding has its own `/api/provisioning/*` surface and UI section, separate from adding a
camera already discovered on the LAN. Every endpoint requires both an authenticated session and
the `require_local_request` dependency. The dependency requires:

- a loopback socket peer;
- a loopback/`localhost` Host header;
- loopback Origin and Referer values when present;
- no cross-site Fetch Metadata; and
- only loopback addresses in `Forwarded`, `X-Forwarded-For`, `X-Real-IP` and equivalent headers.

The label image is decoded in the browser and is not uploaded. The server normalises the device ID,
capability mask, optional MAC and printed version. Wi-Fi passwords use a secret schema type and must
remain request-local: no database, response, application log or job queue may contain them.

The setup UI is an on-demand modal, not a permanent form in the camera list. The server performs a
read-only Wi-Fi scan and returns SSIDs with five-minute signed selection IDs. The browser displays
those names in a non-editable selector; `/provisioning/start` accepts only the signed ID, never
free-form SSID text. A host without an exposed Wi-Fi radio reports the limitation instead of asking
the user to transcribe network details.

The Docker app image includes `iw`, uses host networking and receives only the `NET_ADMIN`
capability needed to scan/associate the host radio; the media container does not receive it. This
does not manufacture a radio inside WSL/VMs: the host still has to expose a Wi-Fi interface.

The synchronization endpoint fails closed with `501` until a real recovered transport exists. The
UI disables that action while `GET /api/provisioning/status` reports `transport_ready: false`.

## Consequences

- A remotely authenticated dashboard cannot provision hardware or submit Wi-Fi credentials.
- Merely hiding a button is not a security control; direct API calls receive `403` too.
- A reverse proxy forwarding a remote client is rejected even though its backend socket is local.
- A proxy that deliberately erases all source headers still fails the local Host/origin checks.
  An administrator who rewrites every signal to impersonate localhost is outside the protection a
  single HTTP listener can provide and must not proxy this path.
- Label inspection can ship before transport recovery without pretending a camera was configured.
