# 0017 — Factory provisioning is a localhost-only security boundary

**Status:** superseded by [ADR 0020](0020-lan-dashboard-and-bluetooth-onboarding.md) · **Date:** 2026-08-11 · **Superseded:** 2026-08-17

## Context

Adding an existing LAN camera only stores RTSP/ONVIF credentials. Factory provisioning is more
powerful: it accepts a household Wi-Fi password and can change nearby hardware. The authenticated
dashboard may be deliberately published through a reverse proxy, so dashboard authentication and
the socket peer alone are insufficient. A proxy on the same host makes every remote peer appear to
the application as loopback.

Browsers also cannot send arbitrary UDP datagrams to a camera SoftAP, so SoftAP belongs behind a
server API. BLE is the exception: Web Bluetooth deliberately exposes GATT only in a secure context,
after a user gesture and a browser-owned device picker. That makes it safer and more portable to use
the user's browser radio than to grant a Docker container access to a host Bluetooth controller.

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

The recovered modern QR encoder is available as the first experimental transport. It renders the
artifact in memory, marks the response `no-store` and returns `awaiting_camera_scan`; artifact
generation is never represented as successful camera configuration. Cameras whose label does not
advertise QR continue to fail closed with `501` because SoftAP transport recovery is not complete.
`GET /api/provisioning/status` reports QR separately as `experimental-ready`.

The APK's BLE GATT contract, fragmentation, response reassembly and AES-CBC semantics have also been
recovered. The localhost modal may now perform read-only discovery and connect to the exact
advertised `GW_BLE_<deviceId>` name through Web Bluetooth. The normal Wi-Fi sequence is challenge
`0x70/0x71`, encrypted network list `0x80/0x81`, encrypted configuration `0x82/0x83`, then plain
finish `0x88`; `0x72/0x73` belongs only to the 4G-device branch. The finish is not an immediate
acknowledgement: the official app calls it from `bleConfigSuccess()` only after the device is
confirmed online/bound. Our flow therefore waits for real LAN discovery before sending it.

The official handshake requires short-lived `tanKey`/`randNumber` material and the configuration
payload embeds a second bind token returned by `POST /openapi/netcfg/cloud/netcfg/genbindtoken`.
The read-only capture helper can now obtain both from a fresh authenticated app session and stores
them in an ignored, owner-only (`0600`) file. The server validates its age and camera identity,
builds the encrypted GATT frames and never returns the Wi-Fi password, TanKey or bind token to the
browser. The modal sends the four recovered stages only after a local user explicitly selects the
exact camera in the browser-owned Bluetooth picker and clicks the Bluetooth configuration button.

This remains an experimental bridge, not LAN-only independence: acquiring fresh handshake material
still depends on the vendor session. By default remote dashboards cannot reach either the modal or
frame preparation endpoint. A deliberately temporary `PROVISIONING_REMOTE_BLE_ENABLED=true` mode
opens only the authenticated BLE subset for a same-origin HTTPS tunnel so a phone can contribute
its Bluetooth radio. The guard requires an HTTPS Origin/Referer matching Host, same-origin Fetch
Metadata and the proxy HTTPS marker; QR and future SoftAP mutations stay localhost-only. Web
Bluetooth retains its independent browser device picker and permission prompt. The operator must
close the tunnel and disable the flag immediately after onboarding.

## Consequences

- A remotely authenticated dashboard cannot provision hardware or submit Wi-Fi credentials unless
  the administrator explicitly opens the temporary same-origin HTTPS BLE bridge.
- Merely hiding a button is not a security control; direct API calls receive `403` too.
- A reverse proxy forwarding a remote client is rejected even though its backend socket is local.
- A proxy that deliberately erases all source headers still fails the local Host/origin checks.
  An administrator who rewrites every signal to impersonate localhost is outside the protection a
  single HTTP listener can provide and must not proxy this path.
- A QR artifact can ship before camera acceptance is proven without pretending it was configured.
- BLE detection can validate real hardware/service compatibility without changing the camera.
- A fresh, owner-only capture can enable one explicit end-to-end BLE provisioning attempt without
  exposing cloud material or household credentials to frontend state.
