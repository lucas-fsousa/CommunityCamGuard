# Stream diagnostics: collection and privacy contract

Checkpoint: 2026-09-27. Tested with fake Docker/API responses only. No diagnostic
collection was run against production; no cameras or containers were operated.

## Use

`bash scripts/diagnose_streams_safe.sh` runs one watcher snapshot and writes
`data/diag/diag-<UTC timestamp>.jsonl`. It preserves the CCG container/API environment
overrides. Python 3 is required (prefers `.venv/bin/python`). It no longer reads the
generated credential config, prints raw go2rtc API/log data, collects argv, or runs
ffprobe against cameras. Thus it does not open an additional camera stream. Any
future direct-media probe must be a separate explicit, controlled test.
The older `scripts/diagnose_streams.sh` is explicitly gitignored. Its local copy was
updated to the same safe wrapper, but is not included in the commit; the versioned
`diagnose_streams_safe.sh` is the documented distributable entry point.

For continued observation use `.venv/bin/python scripts/watch_live_streams.py`.
The rotating output remains `tmp/live-diagnostics.jsonl`. `command` now means an
allowlisted executable label, not its arguments. Recorder/transcoder context comes
from the container and PID; raw encoder flags are intentionally unavailable here.
New diagnostic files are created with restrictive umask 077; historical files and
their permissions are not migrated or scrubbed.

## Contract

- Process sampling asks Docker for `comm`, not `args`, and records only known
  executable names (otherwise `other`). Failure text is fixed.
- Stream API reads are capped at 2 MiB; only opaque camera stream IDs and packet/
  producer/consumer counts are kept. URLs and arbitrary stream names are omitted.
- Browser events are limited to known event names, the configured camera's own
  stream ID, numeric/boolean telemetry and known connection-state labels. Arbitrary
  error text becomes `error_present`; unknown keys/string metrics are discarded.
- Server counters are projected explicitly. The watcher also re-filters historical
  event lines, including those produced before backend deployment, and reads at most
  the last 200 Docker log lines per poll. Bursts can therefore lose older events.
- Public identity/resource metadata (container name, camera ID, timings and usage)
  remain intentional output. This is not anonymization, a hostile-Docker sandbox or
  a guarantee about external process logs. CLI target overrides are operator input.

No timing, encoding, playback recovery or camera control algorithm changed. Unknown
telemetry is dropped rather than trusted. Diagnostic POST failures remain nonfatal
to browser playback. The current dashboard's MSE/RTC metrics are explicitly covered.

## Verification

27 focused tests passed, including fake collector failures, secret-bearing historic
events, process arguments, malformed telemetry and configured-stream validation.
Peak memory 67.1 MiB, no swap (512 MiB / 75% CPU caps). Ruff, bash syntax and mypy
passed; isolated stdlib Python `--help` verified the host tool imports without the
application environment. Actual support-snapshot collection and backend rollout are
separate acceptance steps. Existing historical bundles may still contain secrets.
