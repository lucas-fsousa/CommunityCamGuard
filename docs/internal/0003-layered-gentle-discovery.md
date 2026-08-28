# 0003 — Layered, gentle camera discovery

**Status:** accepted · **Date:** 2026-07-22 (refined through 2026-07-28)

## Context

Cheap ONVIF/RTSP cameras vary wildly and are fragile: their tiny embedded RTSP servers **hang or
drop off the network under connection pressure** (rapid scans, many parallel probes, aggressive
reconnects). Discovery must find them reliably *without* tipping them into that degraded state, and
must not depend on any one mechanism (multicast is often ignored by these units).

## Decision

Discovery is **layered, most-reliable-first**, and deliberately **gentle**:

1. **ONVIF WS-Discovery** (multicast) to find devices where it works.
2. **Active scan fallback** (`discovery/active_scan.py`, stdlib-only): scan the subnet on common
   ports (554 RTSP, ONVIF/HTTP, proprietary).
3. **Credential-free identification** on the no-auth ONVIF device/media services
   (vendor/model/firmware, real RTSP paths, and the authoritative MAC — see ADR 0002). Driver
   selection uses ranked evidence, so a manufacturer/model match outranks a weak port fingerprint.
4. Confirm RTSP with `OPTIONS`, then try the camera-reported paths followed by only the selected
   driver's curated paths. An unidentified/generic host retains the cross-family fallback union.

"Gentle" is a hard constraint: low concurrency, one reused connection per camera, prompt teardown.

## Consequences

- Works on these units where pure multicast discovery does not, and does not knock them offline.
- A curated per-brand path whitelist (not ad-hoc guessing) is the right foundation — an early bug
  where valid paths were missed came from guessing rather than a curated list.
- Adding another driver does not add its RTSP path probes to already-identified cameras. This keeps
  discovery work bounded as the number of supported families grows.
- Operational reality: even so, a camera already in a hung state must be **power-cycled** to recover
  (documented in the README) — no probe strategy fixes a wedged embedded server.
