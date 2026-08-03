# 0007 — PTZ over ONVIF, fire-and-forget

**Status:** accepted · **Date:** 2026-07-27 (latency fix 2026-07-28)

## Context

PTZ on these units was non-obvious. A full port scan finds only **554 (RTSP)**, **5000**, and
**50000**. Two wrong turns: (1) ports 5000/50000 answer nothing to a naive request, so they *looked*
proprietary; (2) a community project suggested PTZ over RTSP `SET_PARAMETER ptzCmd` — our cameras
answer that **200 but never move** (verified by frame-diff vs a still baseline). A 200 there is a
decoy.

The camera is also slow to *answer*: an ONVIF motion verb takes **~700 ms to respond** (TTFB) and the
firmware doesn't keep-alive. Blocking on that response made every command — and every press-and-hold
repeat — cost ~0.7 s, serialising motion and feeling sluggish versus the vendor app.

## Decision

- PTZ is **ONVIF/SOAP over TCP port 5000** at `/onvif/ptz_service`, **without WS-Security**: standard
  `ContinuousMove` (pan `x`/tilt `y` ∈ [-1,1]) then `Stop`, `ProfileToken = IPCProfilesToken0`. This
  moves the hardware for real (confirmed by frame diff).
- The **capability probe is a zero-velocity `ContinuousMove`** (returns 200, moves nothing) — this
  minimal stack implements only the motion verbs; the read-only queries just close the socket.
- Control model: press-and-hold `start`/`stop`, or discrete pulses, so the camera always stops even
  if the client disconnects (no runaway pan).
- **Fire-and-forget the motion verbs** (`_send_soap_nowait`): write the SOAP request, `shutdown(SHUT_WR)`
  (FIN → the camera acts on it immediately — its fixed ~0.4 s step finishes before the 700 ms HTTP
  reply would even land), and drop the socket without reading the reply. The probe keeps the blocking
  path (it needs the real 200, off the hot path).

## Consequences

- Dispatch latency dropped ~200× (`start()` ~700 ms → ~3 ms; end-to-end ~7–15 ms) — PTZ starts on
  press.
- **Rejected:** RTSP `SET_PARAMETER ptzCmd` (a 200-but-no-motion decoy). The remaining limit is
  firmware: a fixed, uninterruptible ~0.4 s step that also ignores `Stop`, so motion is discontinuous
  by construction — true continuous smoothness would need the vendor P2P channel (see ADR 0008).
