# Opt-in continuous native media policy

`MediaPolicy.CONTINUOUS` is an explicit internal enum, independent of the existing
finite SHORT/EXTENDED diagnostic profiles. FINITE remains every default. No setting,
public route, diagnostic or production service currently selects continuous media.
The account worker requires `AccountPolicy.CONTINUOUS` plus explicit TCP admission
before accepting continuous media; finite account/invalid combinations fail closed.

The policy follows the same TCP owner through measurement, AV handshake and receive
lifecycle. It removes healthy-session cumulative lifetime/traffic exhaustion, not
the safety limits: measurement3s, readiness5s, complete-message idle5s, bounded KCP
assembly, parser buffers, fixed per-read/batch sizes and retry/receipt limits remain.
Duplicate measurement/ACK traffic cannot refresh the media-progress deadline.
The cooperative pump keeps its initial receive deadline until readiness; only then
does continuous mode stop using the finite observation/receive cutoff. Explicit
shutdown still limits AVCLOSE to2s before TCP close/B9/UDP cleanup. No reconnect,
second reader or extra authentication is added.

`MediaRateBudget` replaces cumulative admission only in continuous mode. Three
constant-space token buckets allow one second of burst and refill at RX2MiB/s,
TX256KiB/s and4096 framed records/s. Idle credit cannot exceed one burst. These
are initial internal safety limits, not a claim about any camera's maximum bitrate.
Exhaustion or invalid/backwards/nonfinite clock fails closed. Per-packet history is
not retained; parser/consumer memory limits are unchanged. FINITE retains its
previous cumulative budgets and deadlines.

## Local evidence and limits

83 focused tests passed in12.3s,87.4MiB peak/zero swap under192MiB/half-CPU limits.
Ruff and targeted mypy passed (77.1MiB/no swap for type checking).

An encrypted synthetic TCP session processed4500 media messages over15 virtual
minutes, exceeding the old2MiB/4096-record limits, using the same negotiated owner
and no retained parser backlog, then obtained a correlated AVCLOSE receipt.
A cooperative socket fixture ran beyond60 virtual seconds without the short
observation cutoff and closed cleanly. Tests also cover silence despite duplicate
ACKs, measurement timeout, actual rate-exhaustion retirement, typed policy admission,
refill/idle caps, invalid clocks and shared-worker cleanup ordering. Media payloads
in these protocol stress fixtures are not a decoded-video quality validation.

No camera, sound, visual test, build or deployment was performed. The running app
still uses finite native diagnostics and RTSP for production. Next: ongoing
decoder/remux consumer, server-owned continuous source lifecycle and safe fallback,
then a separately authorized physical endurance test. Do not equate this policy
with a completed native live-view integration or proven long-running firmware support.
