# Recording GET / preparation boundary — 2026-09-26

Implemented in source, not deployed. No production files, cameras, encoders or
containers were used. Temporary login remains disabled.

`GET /api/recordings/file` no longer calls `prepare_transcode` on an incompatible
cache miss. It returns 409/no-store with an explicit instruction to POST preparation.
Repeated GET/Range requests, including after eviction, cannot enqueue/restart jobs.
Playback-status also never starts encoding. HTTP preparation remains in authenticated,
origin-checked `POST /recordings/prepare`, preserving admission limits, shared jobs
and 429/Retry-After. Server-side opt-in warming is unchanged.

## Compatibility

- The dashboard already POSTs preparation before assigning the media URL, polls via
  GET and uses POST for native-HEVC fallback. No UI implementation change was needed;
  Node contracts now assert this order.
- Legacy clients must POST `/api/recordings/prepare?path=...`, poll status until ready,
  then GET the file. A 409 alone does not imply a job exists; polling cannot start it.
- Ready cached/native-compatible files retain Range/206, If-Range and 416 behavior.
  `original=true` and downloads remain direct original delivery without encoding.
- Eviction between readiness and media GET can still produce 409. Retry explicit
  preparation (or reselect a failed dashboard item), not repeated GETs.
- GETs are not zero-work: codec-cache misses may invoke bounded ffprobe and cache
  hits update LRU timestamps. Broader read-side resource/redaction review remains open.

## Evidence

95 focused tests passed across HTTP ranges, guarded delivery, archive boundaries and
existing routes. New synthetic cases cover repeated GETs with no job, cross-origin
POST rejection, shared pending preparation, cached ranges, eviction and busy admission
affecting POST only. Node recording playback contracts and ruff passed. Peak test
memory: 87.1 MiB, no swap, capped at 512 MiB/75% CPU. Browser/proxy rollout is pending.
