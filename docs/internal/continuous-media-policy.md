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

## Decoder input backpressure (offline)

`media/nonblocking_input.py` supplies the transport-neutral decoder pipe writer.
It owns the supplied descriptor, sets nonblocking mode and retains at most one
immutable access unit (2MiB). Each caller-driven `pump()` attempts only one16KiB
write. Busy `offer()` returns false: the caller must retain that frame or retire
the epoch, never silently skip a dependent frame. Partial writes preserve order.
The whole access unit has a one-second deadline (configurable up to5s); progress
does not extend it. Full pipe returns immediately. Deadline, invalid clock or
broken pipe closes the descriptor and releases pending bytes. Closing is idempotent.

The writer itself does not spawn ffmpeg or manage decoder output. Its ongoing
consumer integration is described below; it is not a second production live stream.
Pipe tests cover exact partial-write delivery, capacity backpressure, cancellation,
broken readers, invalid clocks/payloads and4500 frames across15 virtual minutes.
These bytes are not a video quality test. No camera access or deployment is needed.

## Ongoing queue-to-decoder validation

`p2p/video_consumer.py` now feeds VideoDelivery into an injected decoder factory.
It is caller-driven on a dedicated consumer, never inside the account/socket
reader. No thread is started implicitly. While input is busy the next frame stays
in the bounded queue. Epoch changes require a configured IDR, confirmed old decoder
shutdown, then a fresh process/pipe. Failed creation, processing or shutdown closes
the queue and fails closed; unconfirmed shutdown cannot start a replacement.
Caller must close the consumer in a finally block. Source closure discards tail
frames by design: this is live validation, not archival frame completeness.

`media/hevc_decoder.py` is a generic real ffmpeg decoder with output discarded.
It cannot play audio, display an image or access camera/network/file protocols.
It uses512MiB address-space/nofile64 limits and one decode/filter thread. The
external supervisor must supply a CPU/RSS quota: thread count is not a CPU quota,
and this continuous process deliberately has no cumulative CPU-lifetime limit.
Each step drains at most4096 progress bytes and writes at most16KiB input; retained
progress is capped at8192 bytes. Only increasing decoded-frame counters refresh
the5s progress deadline; startup permits15s. EOF/exit, malformed/regressing counters,
backwards/nonfinite clock and input/progress stalls retire the process. Cancellation
closes input, terminates/waits0.5s, then kills/waits0.5s if needed. Unknown exit
confirmation propagates rather than allowing another process alongside it.

Run `PYTHONPATH=. python scripts/validate_continuous_decoder.py` inside a resource-
limited scope for the local synthetic check. On2026-10-22 it submitted150 identical
configured64x64 HEVC IDRs and observed147 decoded frames **before EOF** using one
decoder; encoder and decoder run sequentially, no temporary media files. It used
102.9MiB peak/zero swap,4.5s wall time under192MiB RSS/no-swap/half-CPU/30s limits.
The residual frames are not a loss claim: live cancellation deliberately discards
the tail. This proves ongoing queue/input/decoder progress, not real firmware
endurance, visual quality, timestamp synchronization or browser playback.

Production RTSP and protected finite diagnostics are unchanged. Next steps are
browser-compatible output/remux, server-owned source lifecycle/shared-account
coordination and safe fallback. Do not turn on camera tests to repeat this offline
milestone; physical endurance still requires separate authorization.
