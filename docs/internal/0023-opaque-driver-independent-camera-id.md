# 0023 — Opaque, driver-independent public camera identity

**Status:** accepted, public-operation migration implemented · **Date:** 2026-08-27

## Context

The first implementation keyed the registry, API and frontend by MAC. That works for current ONVIF
cameras, but it makes MAC part of the product contract. Other drivers may identify hardware by a
serial number, vendor device ID, certificate or another stable native value. Proprietary P2P also
uses a numeric device ID unrelated to the MAC.

Passing any of those native values through the frontend would spread driver-specific assumptions
through routes, recordings and UI code. It could also select the wrong device when a camera is
re-keyed from an ARP-derived MAC to its authoritative identity.

## Decision

- Give every registry record an opaque public `camera_id` (`cam_` plus a namespaced SHA-256 prefix).
- Derive it deterministically from the best stable identity available at first enrollment. Identity
  namespaces currently include `mac`, `serial` and `vendor_device`, so equal text in two namespaces
  cannot collide semantically.
- Persist the result and never recompute it during operational changes such as IP renewal or MAC
  correction. Existing records are backfilled deterministically from their MAC.
- Return the opaque ID to clients as `camera.id`. New cross-driver routes accept that ID and resolve
  it server-side to the registry row and then to each driver's native identifiers.
- Address camera CRUD operations, capability probing, PTZ, reboot, live-player recovery and browser
  diagnostics by `camera_id`. An exact-MAC resolver remains temporarily at these endpoints for
  clients predating this migration; new clients must not use it.
- Keep MAC as the current discovery/RTSP/recording **internal** implementation key during the next
  migration phase; it is no longer the public identity contract for camera operations.
- Associate proprietary P2P enrollment material with `camera_id`, not MAC or vendor device ID.
  Tokens and native identifiers remain backend-only.

## Consequences

- The frontend can control cameras without knowing whether a driver uses MAC, serial, P2P device ID
  or another native identifier.
- Adding a driver requires an identity adapter and control implementation, not new identity logic in
  the browser.
- A MAC re-key keeps the same public ID and therefore does not invalidate UI references.
- A fresh installation reproduces an ID when it discovers the same identity namespace/value. If a
  future driver can supply a stronger stable identity, it should do so at initial enrollment; an
  existing public ID is not silently replaced.
- The bundled dashboard no longer sends MAC as the target of camera operations. Removal of the
  compatibility resolver and migration of internal media/recording keys are explicit follow-ups.
