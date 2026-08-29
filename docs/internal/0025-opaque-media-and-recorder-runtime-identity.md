# 0025 — Opaque media and recorder runtime identity

**Status:** accepted · **Date:** 2026-08-28

## Context

ADR 0023 established `camera_id` as the public, driver-independent identity, but go2rtc stream names,
runtime status and recorder supervision still used MAC. This leaked the first camera family's native
identifier into shared infrastructure. It also changed stream names and browser references when
discovery corrected a MAC, even though the application's camera identity had not changed.

The recording archive and its SQLite index already contain MAC-shaped directory keys. Rewriting a
live archive safely is a separate data migration and must not be hidden inside a runtime-key change.

## Decision

- Use the validated opaque `camera_id` directly as the base go2rtc stream name; append `_hd`, `_web`
  and `_sub` only for server-local variants.
- Reject MAC/vendor IDs at media helper boundaries. A media stream cannot be created without a valid
  application camera identity.
- Key recorder processes, health/backoff state, logs and `is_recording` queries by `camera_id`.
- Keep segment directories and the current recording-index `mac` column unchanged in this slice.
  This transitional constraint is subsequently removed for new output by ADR 0026.
- At this point a native MAC correction kept the media stream ID stable but restarted the recorder
  to reopen the moved legacy directory. ADR 0026 makes that restart unnecessary.
- Return `id` in the lightweight camera-status response and correlate status in the dashboard by it.
  Keep `mac` in that response temporarily for cached-client compatibility, not as the join key.
- On application startup in external-go2rtc mode, regenerate the mounted configuration **and
  restart go2rtc before starting recorders**. A healthy API alone is insufficient: the external
  process may still expose the pre-migration stream namespace. go2rtc 1.9 may close the restart
  HTTP socket without a response; accept that case only after its replacement API is healthy.

## Consequences

- Media and recording processes no longer assume a camera has a MAC or expose it in stream names.
- A native identifier correction does not invalidate a player URL, quality variant or liveness key.
- Existing footage remains readable without a risky bulk filesystem migration.
- Container restarts cannot leave recorders retrying obsolete stream IDs indefinitely.
- ADR 0026 completes the recording archive/index/filter migration. Status/API compatibility MAC
  fields remain temporarily available to older clients.
