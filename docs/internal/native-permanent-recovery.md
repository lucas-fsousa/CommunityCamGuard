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
commands.

Deployed `f206f6e` / `b-fd856a627844`. The trusted installer restored camera3 at
native revision73, with a private rollback backup and actual old namespace closure
(208.1MiB peak). The existing recordings bind mount was preserved, including the
user's external Windows disk. No recording files were moved/deleted. Host readiness
was paused/live-only; ordinary Docker recording resumed afterward.

An authenticated MSE reader first received1355/226052/223959byte chunks. A precisely
matched SIGTERM then ended only camera3's local pipe-input/x264 encoder PID11.
The running app autonomously created a replacement (observed PID99, one matching
encoder). The first probe raced encoder warmup and received retryable1013; a later
probe received1355/220619/241104byte chunks. Camera3 remained native/active73, and
all three cameras reported online/recording. App StartedAt stayed
2026-11-04T09:15:34.734200274Z, restart count0, no OOM. No Docker restart, camera
reboot or physical action was used for this fault injection/recovery.

The original full CI found four test expectations that omitted the new background
`preserve_current=True` argument. Updated that contract without weakening siren
scope assertions;11 migration tests passed (97.2MiB). Image41805651733 passed.
Follow-up `5f8f774` passed full CI41806410030:5639 tests, one existing skip,
all Node contracts, Ruff and Mypy. The newest camera3 original archive was
1,572,944bytes and updated2.7s ago on the preserved recordings volume, confirming
recording resumed after the encoder fault as well as live view. This
accepts local-encoder recovery, not all possible auth/unknown-cleanup failures or
multi-day stability. The unavailable message still legitimately applies when
credentials/review are revoked or ownership retirement cannot be confirmed.
