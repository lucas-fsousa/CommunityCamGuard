# Native source stranded offline — 2026-11-04

Observed on the installed `b-3422b2883cd9`: camera3 native/blocked revision68,
offline/not recording; other cameras legacy0 online/recording. Its capability
snapshot generation90 contained NULL evidence/timestamps, so its reviewed offer
could not be reconstructed. The old failure was no longer present in the retained
Docker access logs. These facts do not prove which event originally ended media.

Two reproducible dead ends are corrected:

- Local FFmpeg MP4 order/capacity failures and child exit were terminal despite
  confirmed consumer/thread/process/socket/outer-route retirement. The driver now
  retries those typed local failures with the existing sequential2–60s backoff.
  Parser memory limits, validation and HD/archive settings are unchanged. Unknown
  cleanup, authentication rejection and changed/revoked evidence still block.
- Opportunistic legacy capability collection previously called begin(), clearing
  recorded identity before network I/O. Failure could strand native admission.
  Background collection now publishes atomically against the original generation,
  preserving previous evidence/expiry on failure. An explicit invalidation during
  collection still wins; no stale control permission is extended. Initialization
  or repair of an already empty record requires successful collection first.

Explicit local network setup failures (broker heartbeat/certification availability,
inventory timeout/unavailability and exhausted preparation budget) now reconnect
after the existing confirmed socket closure. Auth rejection/unknown stages do not
gain automatic credential renewal or capability grants.

`data/logs/native-events.jsonl` retains safe terminal facts separately from HTTP
polling noise (512KiB plus two rotations). Fixed labels and booleans only; no raw
vendor errors, credentials, packets or media. Evidence-triggered cancellation is
recorded too, unlike the old stderr-only failure path. Log failure cannot abort
retirement.

302 focused tests passed under192MiB/no swap/50% CPU, largest peak156.9MiB;
Mypy379/Ruff passed. Regressions feed real malformed local MP4 into the parser,
confirm old epoch retirement before replacement, and verify that a failed metadata
refresh cannot clear the live identity. All tests use synthetic data, no hardware
commands. Deployment and a camera3 encoder-only recovery check follow.
