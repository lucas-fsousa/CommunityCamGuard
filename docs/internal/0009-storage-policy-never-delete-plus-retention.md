# 0009 — Storage policy: a never-delete monitor + opt-in retention

**Status:** accepted · **Date:** 2026-07-28

## Context

24/7 recording grows without bound. Two different needs pull apart: (a) never lose footage to a
surprise auto-delete, and (b) not fill the disk, and (c) optionally cap how long footage is kept. A
single "delete when full" policy conflates them and risks deleting footage the user wanted.

## Decision

Two **orthogonal** mechanisms:

1. **Storage monitor** (`recording/storage.py`) — guards the *ceiling* by **fullness**, and by policy
   **never deletes**. It alerts at `storage_alert_percent` (default 80%), **stops saving but keeps
   streaming** near full, and **resumes automatically** once usage drops back below the resume mark.
2. **Retention job** (`recording/retention.py::RetentionCleaner`) — trims by **age**. A background
   thread runs hourly (retention is day-granular) and deletes every segment older than
   `RECORDING_RETENTION_DAYS` (default 7; **0 = keep forever**, no upper bound). It removes the file,
   its playback-cache transcode, and its index row, then prunes empty `<mac>/<day>/<hour>` dirs.

Retention is **the only place the app deletes footage**, and only because the user set a window. It is
**index-driven** (the recordings index is authoritative, so it removes exactly what the UI can list)
and self-healing (a file that can't be unlinked keeps its row and is retried next pass).

## Consequences

- The two knobs never fight: retention trims by age, the monitor pauses by fullness. Both are gated by
  `autostart_services`, and `start()` is a no-op when disabled (no idle thread).
- The Recordings page surfaces the active window (`retention_days`, `0` = unlimited) so users
  understand why old clips disappear.
- New segment paths and indexed start times are explicit **UTC** (ADR 0016), so the age cutoff is
  UTC-aware too. Legacy naive rows remain comparable by their ISO calendar components; they are not
  rewritten because their original timezone cannot be inferred safely.
