# 0026 — Opaque recording archive identity without destructive history moves

**Status:** accepted · **Date:** 2026-08-28

## Context

After ADR 0025, live streams and recorder processes used `camera_id`, but new segments still landed
under `recordings/<mac>/`, the index filtered by MAC and the dashboard joined names by MAC. Correcting
a native MAC therefore required renaming live archive directories. A future driver may not have a
MAC at all, and moving historical footage during discovery creates unnecessary data-loss risk.

Existing installations already have large MAC-directory archives. Their absolute indexed paths are
valid and should not be rewritten merely to improve identity semantics.

## Decision

- Write new segments under `recordings/<camera_id>/<UTC-day>/<UTC-hour>/...`.
- Add a non-null `camera_id` column and index to the recording table. On startup, backfill legacy
  rows only when their normalized MAC exactly matches a registered camera; leave orphan rows intact.
- Teach the indexer to recognize both opaque-ID directories and historical compact-MAC directories.
  Conflict updates never erase an already known canonical owner when an old directory cannot be
  resolved after a native MAC correction.
- Filter the API/dashboard by `camera_id` and resolve friendly names by it. Keep the old `mac` query
  and column as a temporary compatibility projection and fallback for orphan historical footage.
- Never rename archive directories during a MAC correction. `rekey_segments` now updates only the
  deprecated indexed MAC projection and fills canonical ownership when possible.
- Resolve download filenames from indexed `camera_id`, with directory/MAC fallback for an unindexed
  in-progress or orphan legacy segment.

## Consequences

- A camera's live stream, recorder, new footage and UI filter share one driver-independent identity.
- Native identifier corrections no longer interrupt playback/recording or move user data.
- Existing archives remain readable in place and progressively gain canonical ownership safely.
- The compatibility `mac` column can be removed only after the old API/client support window closes;
  orphan legacy footage will need an explicit operator-association workflow before that removal.
