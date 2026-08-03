# 0012 — Digital zoom (there is no optical zoom to drive)

**Status:** accepted · **Date:** 2026-07-28

## Context

Zoom was a requested feature. Before building anything, we **measured whether the hardware has it**,
with the same frame-diff (PSNR) method that proved PTZ real and exposed the RTSP `ptzCmd` decoy:

| test | PSNR | reading |
|---|---:|---|
| still scene, no command | 27.9 | baseline |
| ONVIF `ContinuousMove` **Zoom x=1.0** ×5 | 28.2 | **unchanged — no actuation** |
| ONVIF `ContinuousMove` PanTilt (control) | 13.4 | collapses — real motion |

The ONVIF **Zoom verb answers 200 and is a decoy**, exactly like the RTSP `ptzCmd` path. The vendor
app agrees: its `ZoomView`/`_OnGesture` are **renderer** calls — its own zoom is client-side.

## Decision

Zoom is **digital**: a CSS transform on the player. The go2rtc player is a **cross-origin iframe** we
cannot script — but transforming the iframe *element* needs no access to its content, so the feature
is a `translate(...) scale(...)` on the tile's frame plus clamped pan offsets.

- A `.zoom-overlay` carries drag-to-pan, wheel-to-zoom (about the pointer), and double-click-reset.
  It is `pointer-events: none` **at 1×** so the go2rtc player keeps its own controls (click to unmute
  for listen-in, ADR 0011) and only takes the pointer once zoomed — the compromise that lets both
  interactions share one cross-origin embed.
- Pan is clamped to `(scale-1)/2` per side so empty space can't be dragged into view; returning to 1×
  re-centres. State is per-camera and re-applied on `camFrame()`, so a player restart or a
  Recordings→Grid resume keeps the zoom.

## Consequences

- Zoom works on **every** camera (pure rendering) — unlike PTZ it is not capability-gated.
- Frontend-only, no backend/stream cost. It cannot recover real detail — it's an enlargement of the
  delivered pixels (the picture ceiling is still the camera; see ADR 0005).
- **Rejected:** driving ONVIF/RTSP zoom verbs (measured decoys).
