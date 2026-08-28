# 0010 — Re-key a camera to its authoritative MAC + retroactive capability backfill

**Status:** accepted · **Date:** 2026-07-28

## Context

ADR 0002 made discovery prefer the camera's own ONVIF MAC over the ARP-derived one — but only for
*new* candidates. A camera already registered under its ARP MAC then came back, once ONVIF answered,
under a **different key**: `reconcile()` saw an unknown MAC and offered the same physical camera as a
**brand-new candidate**, while the original record (name, password, capabilities) went stale and
unmatchable. Separately, probe-on-add wasn't retroactive: cameras added earlier kept empty
capabilities, so their controls stayed dark until someone pressed "probe" by hand.

## Decision

Close both on the one code path that surfaces them — a scan reconciling against the registry:

- `ScannedHost` carries **both** `mac` (authoritative ONVIF) and `arp_mac`; keeping the ARP value is
  what lets the registry still recognise the old identity.
- `registry.rekey_camera(old, new)` originally moved the row's primary key, preserving
  name/credentials/caps. Since ADR 0027 it updates only the optional native MAC while the
  `camera_id` primary key remains stable. It
  **refuses when the target MAC already exists** (a genuinely different camera — merging would discard
  one record's credentials).
- `reconcile()` re-keys when the ONVIF MAC is unknown but the ARP MAC is registered, and reports the
  move via an **`on_rekey(old, new)` callback** — keeping layering honest (the registry must not
  import the recording layer; the caller wires them).
- **Recordings followed the camera** initially through `recorder.rekey_segments(old, new)`, which
  renamed MAC directories and repointed the index. ADR 0026 supersedes that mechanism: canonical
  ownership and new directories use `camera_id`; the helper now updates only the deprecated MAC
  projection and never moves archive files.
- **Capability backfill**: on a scan, any configured camera that returns with no capabilities and a
  known IP is re-probed via the shared `_probe_and_store` — best-effort per camera, so one timing out
  never fails the scan. A scan is the right moment (the camera just answered; it's already the slow,
  user-initiated path).

## Consequences

- A camera that gains an authoritative MAC keeps its identity, credentials and full recording history —
  no duplicate candidate, no stranded footage. Verified through the real scan route.
- Since ADRs 0025/0026, re-keys do not restart media or recording services: stream, process and new
  archive identities remain stable, and historical paths are deliberately left untouched.
