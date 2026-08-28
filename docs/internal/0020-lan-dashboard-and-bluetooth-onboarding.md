# 0020 — LAN dashboard and homologated Bluetooth onboarding

**Status:** accepted · **Date:** 2026-08-17

## Context

ADR 0017 restricted all provisioning to loopback because the operation receives a household Wi-Fi
password and changes nearby hardware. Physical testing later required a phone to contribute its
Bluetooth radio. A public HTTPS tunnel proved the BLE transport but is not an appropriate permanent
path for an otherwise local monitoring system. The dashboard also needs to be usable from phones
and computers already on the household LAN.

Web Bluetooth adds a separate constraint: browsers expose it only from a secure context. Binding
the app to `0.0.0.0` makes normal HTTP dashboard access work on the LAN, but does not make a private
IP HTTP origin secure.

## Decision

The authenticated dashboard/API binds `0.0.0.0:3200` by default. Unauthenticated go2rtc API,
WebRTC and RTSP restream ports remain loopback-only and browser media continues through the
authenticated same-origin app proxy.

Provisioning remains more restricted than ordinary dashboard access. Its backend guard accepts
only loopback, RFC1918 IPv4, IPv6 ULA or link-local peers using a matching literal local Host and
same-origin Origin/Referer. It rejects DNS names, cross-site fetch metadata and any public address
in forwarding headers. The explicitly opted-in same-origin HTTPS-tunnel exception remains limited
to BLE and is disabled by default.

BLE onboarding is accepted as physically homologated for the tested firmware. The implementation
matches the vendor client's MTU 256, short-lived challenge/TanKey exchange, encrypted camera-side
Wi-Fi scan and configuration, and finishes after the camera confirms Wi-Fi association. The
browser device supplies Bluetooth; the server does not need host Bluetooth access.

## Consequences

- LAN users can monitor and administer through `http://<server-private-ip>:3200` after login.
- Only port 3200 is exposed; internal media-control ports are not widened.
- BLE from another LAN device still needs trusted HTTPS. `localhost` on a Bluetooth-capable server
  or a temporary HTTPS tunnel are the current practical alternatives.
- Dashboard authentication is mandatory and the deployment must replace the example secret.
- The camera can join Wi-Fi without using the vendor setup UI. The backend now creates and renews
  its own vendor-authenticated session and fetches fresh handshake material without Android,
  Frida or capture files. Removing the vendor WAN/account dependency itself remains P0/P1 work.
- LAN discovery, privileged P2P enrollment, RTSP activation/credentials and application-registry
  insertion are separate stages; Wi-Fi success must not imply any of them.

## Validation addendum — 2026-08-24

The same factory-new test camera completed the whole reconstructed path without using the vendor
application as a runtime gateway:

1. BLE configured Wi-Fi and the matching `devresult` lookup confirmed it online.
2. An explicit bind returned subscription material; a fresh P2P session listed the third camera
   online and read its model inventory.
3. `onvifEn=1` opened local RTSP. The official APK trace proved that modern command `type=3`
   receives the lowercase HA1 `MD5("admin:HIipCamera:<password>")`, not the clear password.
4. ACK was treated only as delivery. The clear password was accepted only after Digest RTSP
   returned real media over UDP on `/onvif1` and `/onvif2`.
5. The camera was inserted through the normal camera API. Its clear credential is encrypted in
   the registry; go2rtc exposes one shared camera producer to the recorder and browser transcodes.
6. The H.264 1920x1080 browser variant and the first UTC recording segment were both verified.

The production modal now executes the homologated continuation as a bounded transaction:
`bind -> session -> onvifEn -> HA1 -> media-packet proof -> registry`. No ACK alone advances the
registry and no clear credential is persisted before media proof. A durable interrupted bind can
be resumed without rebinding, and an already verified camera is not assigned another password.
This implementation checkpoint still needs a fresh-camera live validation.

### Production P2P extraction addendum

The first production slice after homologation makes the bind durable and proves authentication
without touching camera state:

- the bind transaction validates the 64-byte account access token, unsigned access ID and 128-hex
  device subscription token, then stores them as one Fernet-encrypted SQLite payload before it
  consumes the one-time BLE/cloud handoff;
- status after a process restart is based on successful decryption/validation, not merely on a row
  existing;
- `vendor_p2p` contains the recovered GAT authentication, gute/RC5 crypto, list, certification,
  init-info parsing, heartbeat, selected-target TermDNS and A4/A3 plus CA/CB rendezvous primitives
  needed by the production backend;
- `/provisioning/privileged/p2p-probe` reports only aggregate counts and whether the requested
  target is visible/online. It never exposes tokens or identities of other cameras;
- `/provisioning/privileged/p2p-route-probe` is the explicit next boundary: it contacts only the
  selected online camera, proves broker acknowledgement, route advertisement and direct CA/CB, and
  exposes no peer endpoint or ephemeral session identifiers;
- both probes stop before subscription, thing-model access, application commands or media. A live
  2026-08-24 validation authenticated three devices and completed the selected camera's direct
  handshake with no broker error.

The read-only slice `/provisioning/privileged/p2p-property-read` accepts only the
fixed B7 allowlist recovered from the APK, selects the durable enrollment's exact device ID and
requires a direct handshake before issuing one property read. No arbitrary D2 writer or AC action
constructor is exposed. Live validation against the designated test camera returned
`ProWritable.videoParm` with transport ACK/error zero and did not contact the two monitoring
cameras. The production driver now has one separate, fixed-path D2 orientation operation accepting
only normal/180°, with a mandatory B7 preflight, matching D3 response and fresh B7 readback. It is
exposed through the typed vendor-control HTTP/UI surface. This endpoint uses the strict trusted-LAN guard, not
the opt-in remote HTTPS exception reserved for the Web-Bluetooth onboarding subset.

An enrollment completed by an older running container remains memory-only; it must be bound once
through the new version before restart durability can be claimed. No process-memory extraction or
secret-returning compatibility endpoint is introduced.

### Native account/bootstrap addendum

Static APK analysis identified the exact login response identities used by provisioning:
`terminalId` is sent as the bind-token `termId`, while the signed Java `userId` is converted to
the unsigned server user ID by setting its high bit. The production backend now implements:

1. anonymous signed account login and renewable authenticated sessions;
2. encrypted-at-rest storage of the account digest, 64-byte token and provisioning identities;
3. signed `getTanKey` and `genbindtoken` requests for the selected camera; and
4. direct in-memory construction of `BleProvisioningMaterial` consumed by the existing Web
   Bluetooth codec.

The complete `login -> refresh -> TanKey -> bind token -> backend material` chain was validated
against the live vendor service using a fresh native session and a new output context. It used no
Android process, emulator, Frida capture or previous BLE material. Production prefers this native
account source; the owner-only research file remains only as a compatibility fallback.

This milestone removes the APK from runtime, not the vendor cloud. The matrix is deliberately
explicit:

| Layer | Current role | Runtime requirement |
|---|---|---|
| APK/Frida | Research oracle for recovering still-unknown contracts | No |
| Native backend + vendor cloud | Account login/refresh, TanKey, bind token and P2P bootstrap | Yes, currently |
| LAN-only backend | Target architecture when cloud bootstrap has been replaced | Not complete |
