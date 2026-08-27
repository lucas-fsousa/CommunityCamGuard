# 0022 — Resilient recording directory rollover

**Status:** accepted · **Date:** 2026-08-27

## Context

Two recorders stopped together at an hourly boundary. Their logs showed FFmpeg failing to open the
new UTC hour with `No such file or directory`; no later folders appeared until a container restart.
The segment muxer cannot create directories itself.

Directory creation, FFmpeg supervision and SQLite indexing ran sequentially in one unguarded
maintenance thread. One transient exception could therefore kill directory preparation and the
supervisor along with the non-critical indexing pass. Retention also pruned every empty directory,
including the next hour prepared by the recorder.

## Decision

- Catch and log failures independently for directory preparation, FFmpeg supervision, log upkeep
  and indexing; every responsibility is retried on the next maintenance pass.
- Prepare the current UTC hour plus 24 future hours for every configured recorder.
- Let retention remove only empty hour directories strictly older than the current UTC hour;
  preserve current/future reserve directories.
- Keep FFmpeg's child timezone pinned to `UTC0`, matching the directory horizon.

## Consequences

- A transient database/index/filesystem error cannot permanently stop the maintenance thread.
- Recording can cross a full day of unexpected maintenance disruption before directory exhaustion.
- Retention no longer races with the next segment rollover.
- Empty future directories are intentional, bounded metadata and contain no recording data.
