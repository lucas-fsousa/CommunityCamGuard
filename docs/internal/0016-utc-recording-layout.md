# 0016 — Recording paths and index timestamps are always UTC

**Status:** accepted
**Date:** 2026-08-11

## Context

The segment muxer expands `%Y/%m/%d/%H` using the FFmpeg process timezone. The application happened
to run in a UTC container, but neither host execution nor a future Compose `TZ` override guaranteed
that. A timezone change could therefore split one continuous archive across different calendar
directories, duplicate an apparent hour at DST fallback, or make retention compare different clock
bases. Camera timestamps are also unreliable and may change when the vendor app synchronizes the
device clock.

## Decision

- Recording paths are defined as UTC:
  `recordings/<mac>/<YYYY-MM-DD>/<HH>/<YYYYMMDD_HHMMSS>.mp4`.
- Each recorder FFmpeg child receives `TZ=UTC0`; this controls the segment muxer's `strftime`
  expansion even outside Docker and does not require zoneinfo files.
- Directory pre-creation uses `datetime.now(UTC)` so it agrees with FFmpeg at hour/day rollover.
- Parsed filenames are indexed as timezone-aware ISO 8601 (`+00:00`), and retention computes its
  cutoff in UTC.
- Compose also declares `TZ=UTC` as a visible deployment default, while correctness remains in the
  recorder itself.

## Consequences

Changing camera, browser, host or container timezone cannot alter the on-disk namespace for new
segments. UTC has no daylight-saving duplicates or missing hours. Presentation layers may convert
UTC to the user's preferred timezone, but persistence and filtering remain unambiguous.

Existing files and naive index rows are not renamed or reinterpreted automatically: the timezone
that created a historical filename cannot be recovered reliably. They remain readable and indexed;
the UTC invariant applies to newly produced segments after deployment.
