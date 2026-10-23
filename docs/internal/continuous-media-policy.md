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

`media/hevc_decoder.py` defaults to a generic real ffmpeg decoder with output discarded.
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

Production RTSP and protected finite diagnostics are unchanged. Physical endurance
still requires separate authorization; do not repeat camera tests for this milestone.

## Opt-in H.264 fragmented MP4 output

Passing a fresh `FragmentedMp4` collector to `HevcDecoder` replaces null output with
H.264 baseline/yuv420p encoding, ultrafast/zerolatency, GOP25 and no resizing. This
is a transcode, not HEVC copy/remux. It uses the same process/input/cleanup lifecycle,
not a second encoder. Progress moves to stderr (ffmpeg logging disabled); media
stdout is read nonblockingly at most16KiB per step. Null-mode behavior is unchanged.
No audio is encoded; the output is not yet a synchronized audiovisual stream.

The thread-confined collector frames top-level ftyp+moov initialization and moof+mdat
fragments. It caps combined partial/paired/queued bytes at2MiB and queued chunks at8.
Unknown order, unbounded/extended box sizes, oversize data or a full queue fails
closed, clearing retained bytes. Payloads are omitted from repr. This is a parser
for our local muxer's framing, not a full validator for arbitrary MP4 internals.
Each collector is claimed once; even an empty collector cannot be shared by two
decoders or reused across epochs. Consumers must obtain the new initialization
segment on replacement. Cancellation discards pending fragments, not archival flush.

`PYTHONPATH=. python scripts/validate_continuous_decoder.py --mp4` runs the offline
check under external resource limits. It generated150 synthetic64x64 HEVC IDRs,
observed146 frames of transcoder progress before EOF, and collected3748bytes of
completed MP4 output. After process cleanup, ffprobe decoded125 H.264/avc1/yuv420p
frames at64x64 from these fragments. This run took4.5s,53.9MiB peak/zero swap under
192MiB/no-swap/half-CPU/30s limits. Tail differences reflect GOP buffering and
cancellation, not a measured network loss. This small fixture does not establish
1080p resource cost, sustained firmware compatibility, browser support or latency.

The existing dashboard still uses go2rtc/RTSP. No public route, camera operation,
build or deployment was added by this output stage.

## Shared fragment fan-out (internal, offline)

`media/mp4_fanout.py` retains one initialization segment (<=256KiB) and up to8
fragments under a combined2MiB cap. Up to16 subscriptions hold only sequence cursors,
not copied media buffers. Publication never waits for readers: evicting unread
fragments retires only lagging subscriptions with `SlowConsumerError`. New readers
receive initialization then the newest completed keyframe fragment. This relies on
the trusted local `frag_keyframe` muxer, not arbitrary externally supplied fragments.
Each poll returns the existing immutable chunk reference. One short lock protects
the ring/cursors, with no I/O or callbacks under that lock.

The hub is single-epoch. Repeated init is an error, closes all subscriptions and
clears retained bytes; a decoder restart requires a fresh hub. Explicit per-reader
close frees its slot without changing the producer or other readers. Hub close
invalidates every reader. Joining before a complete fragment is available fails
without closing the producer. Invalid/oversized publication fails closed.

The synthetic `--mp4` operator now passes output through this hub and two readers.
It used one transcoder, delivered the same3748byte count to each reader, and decoded
125 H264 frames from the first reader's concatenated stream after cleanup. The run
took4.3s,45MiB/no swap with the same192MiB/half-CPU/30s external limits. Unit tests
also exercise4500 fragments with16 readers, shared object identity, byte/count
eviction, isolated cancellation, late join, capacity and epoch retirement.

This does not implement authenticated browser delivery. A future endpoint must
authorize each subscription, terminate it on revocation/expiry, bound pending
transport writes and close it on disconnect. References retained by consumers are
outside the ring's accounting. There is no public route and no per-browser encoder.
Next: server-owned source lifecycle/shared-account coordination, authorized delivery,
audio/timestamps and safe RTSP fallback, followed by separately authorized endurance.

## Driver-owned continuous media admission

`SharedReadSession.run(video_delivery=...)` now explicitly selects continuous media
through `AccountReadLifecycle.run_media`. Default identity-only runs are unchanged.
The route is constructed from the freshly authenticated exact online device and
registered enrollment, not from a browser-supplied native target. Account and media
policies are both CONTINUOUS; motion admission is rejected on this lifecycle.
The same account worker owns UDP, media-route sequencing and the cooperative TCP
pump. No decoder runs on that reader and no second authentication is started.

The driver retains the legacy nonwaiting handoff reservation through AV CLOSE,
physical TCP close, broker B9 release and physical UDP close. Foreground contention,
explicit cancellation, credential/enrollment change and consumer queue closure all
yield the session without automatic reconnect. Media cleanup uncertainty now checks
the full lifecycle barrier, not only UDP close; missing TCP/B9 receipts or physical
close failure blocks subsequent legacy authentication. Early cancellation/busy/
enrollment failures retire the supplied queue. Rejected concurrent lifecycle runs
do not close an active generation's queue.

47 focused tests passed under192MiB/no-swap/half-CPU limits (92.9MiB peak). These use
real protocol state machines over deterministic encrypted socket fixtures, not
cameras: continuous policy/queue propagation, yield reasons, cleanup ordering and
all four cleanup fault barriers passed. Targeted mypy passed at104.8MiB/no swap.
This admits media into the server-owned account lifecycle but does not yet start
the consumer, expose browser subscriptions or enable production source switching.
