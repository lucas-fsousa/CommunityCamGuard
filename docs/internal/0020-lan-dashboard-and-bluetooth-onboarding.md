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
- The camera can join Wi-Fi without using the vendor setup UI, but fresh handshake material
  still comes from a vendor-authenticated session. Removing that dependency remains P0/P1 work.
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

This validates the wire contract, not yet a single-click product flow. The production modal still
ends after bind and reports RTSP as pending. The next implementation stage is a bounded backend
P2P client that executes `bind -> session -> onvifEn -> HA1 -> media proof -> registry` as an
explicit, observable transaction. No ACK alone may advance it, and no clear credential may be
persisted before media proof.

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

An enrollment completed by an older running container remains memory-only; it must be bound once
through the new version before restart durability can be claimed. No process-memory extraction or
secret-returning compatibility endpoint is introduced.
