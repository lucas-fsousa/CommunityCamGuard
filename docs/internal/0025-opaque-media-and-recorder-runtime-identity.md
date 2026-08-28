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
  The recorder carries the selected camera record so it can combine an opaque runtime/source key
  with the compatible legacy output path.
- On a native MAC correction, keep the media stream ID stable but restart that camera's recorder so
  FFmpeg reopens the directory moved by `rekey_segments`.
- Return `id` in the lightweight camera-status response and correlate status in the dashboard by it.
  Keep `mac` in that response temporarily for cached-client compatibility, not as the join key.

## Consequences

- Media and recording processes no longer assume a camera has a MAC or expose it in stream names.
- A native identifier correction does not invalidate a player URL, quality variant or liveness key.
- Existing footage remains readable without a risky bulk filesystem migration.
- A follow-up must migrate the recording archive/index/filter contract to `camera_id`, then retire
  `rekey_segments` and the status/API compatibility MAC fields.
