# 0001 — Pluggable camera drivers

**Status:** accepted · **Date:** 2026-07-27

## Context

The founding goal is a **generic** dashboard where adding a brand/model reuses the existing
structure. Early on that eroded: Yoosee-specific ONVIF logic accreted across `control/` and a
`discovery/capabilities.py`, coupling the engine to one family and making a new brand a cross-cutting
change.

## Decision

All camera-family knowledge lives in one plug-in unit: a **`CameraDriver`** subclass under
`backend/app/drivers/`. A driver bundles the family's RTSP discovery paths, family detection
(`matches`), capability-probe hooks, and controls (`ptz`, `reboot`, …). Everything else is generic
and speaks only to this interface:

- **discovery** takes its path list from `drivers.rtsp_paths()` (union of all drivers, common first);
- **probe** = `drivers.probe(camera, open_ports)` — detects the driver (by vendor + open ports) and
  runs it; the base handles the shared RTSP-SDP part (tracks/codecs), families override `_probe_controls`;
- **API** routes PTZ/reboot through `drivers.for_camera(camera)`; anything a driver doesn't override
  raises `Unsupported` → HTTP **501** (honest, no dead buttons);
- the camera stores its resolved `driver` key in its capabilities JSON.

Low-level protocol **toolboxes stay shared** (`control/ptz.py`, `control/device.py`,
`discovery/rtsp.py`); a driver just wires them for its family, or adds a new toolbox for a non-ONVIF
brand. Registration is one entry in the ordered `DRIVERS` tuple (most-specific first, `generic` last).

## Consequences

- **Adding a brand = one file in `drivers/` + one line in the registry** — no engine changes
  (see `CONTRIBUTING.md`).
- Partial drivers are fine and honest: unimplemented controls surface as `501`, not broken UI.
- The former `discovery/profiles.py` and `discovery/capabilities.py` were removed — drivers supersede
  both. Shipped: `yoosee` (full ONVIF PTZ + device info), plus `dahua`/`hikvision`/`xiongmai`/`generic`
  (discovery today; controls are a one-method add once the hardware is tested).
