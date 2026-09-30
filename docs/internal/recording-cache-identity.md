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
