# Recording cache source identity — 2026-09-30

Review found that derived MP4 names depended only on the source path. A growing or
replaced recording could therefore reuse a conversion made from an older version
of the same path. This is a demonstrated cache-consistency defect, not proof of
the cause of every reported playback delay.

The cache now uses a versioned hash of the resolved source path, device, inode,
size and nanosecond modification/change timestamps. Metadata only: no whole-file
hashing or media buffering. Missing originals and empty derived files are not hits.
Source identity is rechecked around lookup and before/after encoding. A changed
or removed source fails that job with the content-free reason `source_changed`;
its partial output is removed and encoder/job ownership is released. A complete
result is atomically published only after the source check. Shared per-path job
admission, single encoder, timeouts and original-quality recording stay unchanged.

This is not a filesystem snapshot or protection against arbitrary privileged
concurrent tampering. A source modified immediately after the final check gets a
new lookup key; the just-published old version is no longer selected on later hits.

Old path-only cache files cannot establish freshness and are not reused. They are
not eagerly deleted: ordinary cache LRU policy still governs derived-file removal.
The first compatible playback of an old entry may need one fresh conversion. No
bulk re-encoding, background warmer activation or original deletion is introduced.

Synthetic tests cover growth/replacement/removal while queued and during encoding,
cache invalidation, empty-cache rebuilding, missing originals and existing encoder
budget/logging behavior. No camera or real encoder is used in these tests.

## Codec probe admission

Concurrent cold requests previously could each launch ffprobe for the same file,
before reaching the encoder budget. Metadata lookup now serializes each resolved
path using 32 fixed lock stripes and permits at most two probes per server process.
Successful concurrent lookups reuse the identity-checked LRU entry. Stripe
collisions may wait but cannot share another recording's metadata; there is no
growing lock registry, worker pool or media buffer. The global metadata lock is
never held during subprocess I/O. Probe exceptions release both admission locks;
unknown results and failures remain uncached, not fabricated successful results.

The production subprocess timeout remains ten seconds per probe. Admission now
has one shared monotonic one-second budget across stripe and subprocess-slot waits.
Exhaustion raises `PlaybackBusy`, releases held admission resources and becomes
HTTP 429 with `Retry-After: 5` on prepare (including native HEVC negotiation), status
and compatible-file requests. It is never converted into an empty/unknown codec or
successful readiness. Explicit originals/downloads bypass probe admission. The
dashboard already reports preparation/status 429 without automatic resubmission.
Tests cover saturation/recovery and the HTTP boundaries. This bounds inspection
pressure and admission wait, not total first-frame latency. Limits are
per process, not a cross-worker/distributed budget. Complete-file HEVC conversion
is unchanged, with one encoder and no automatically enabled warmer. Synthetic
concurrency tests cover same-file reuse, two distinct simultaneous probes and
exception cleanup, without invoking ffprobe or accessing cameras.

The optional warmer now uses the same cache-validity check: an empty derived file
is pending work, not a completed conversion. Its inspections explicitly do not
touch cache modification times, so merely scanning recent archives cannot promote
unused entries in LRU. A synthetic regression covers both behaviors. The warmer
remains opt-in and disabled by default.

## Validation and rollout

Commit `594660c` passed the complete GitHub CI (run `36791566646`), including
ruff, mypy, Python and frontend contracts. The focused local suite passed 53
tests under a 256 MiB/no-swap/50%-CPU cap, with 94 MiB peak and zero swap.
The image was rebuilt with a 512 MiB/no-swap/75%-CPU build limit; only the app
service was recreated. Runtime build `b-2ceb15b4dbc8` reported all three cameras
online and recording. The first immediate health request preceded HTTP startup;
the subsequent check passed. Both containers reported OOM=false and go2rtc's
start time was unchanged. No physical camera control or bulk preparation ran.

These checks establish admission/cache behavior and deployment health, not a
new end-to-end latency measurement or proof that every recording stall is fixed.
