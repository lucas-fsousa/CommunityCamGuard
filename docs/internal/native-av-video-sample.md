# Bounded native-video sample foundation — 2026-09-22

`p2p/av_sample.py` adds a diagnostic-only in-memory collector and the socket probe
accepts it explicitly via `sample=`. Default production and diagnostic invocation
remain count-only: the route owner and HTTP endpoint do **not** opt in yet.
The collector does not create a connection, decoder, file or background worker.

## Bounds and ownership

- Video only; inbound audio is not copied. Maximum 2 MiB and 120 complete records.
  At 120 frames collection stops, while the owner can finish transport/teardown.
  Byte overflow fails and clears the sample rather than accumulating a partial
  access unit. Socket duration and traffic budgets remain unchanged.
- Initial scope is codec 5/HEVC, positive dimensions up to 1920×1080. This is an
  experimental decoder budget, not a general driver capability/quality limit.
- Uses the shared transport-neutral HEVC gate: initial VPS/SPS/PPS plus IDR in one
  complete access unit; dependent frames before that are discarded. Corruption,
  timestamp regression, configuration changes and second decoder epochs after
  collection starts are fatal. No concatenation across discontinuities.
- Keeps only first/last accepted raw timestamps, reporting their relative span in
  ticks. It does not assume a clock rate, frame rate or wall-clock time.
- Successful sample ownership stays with the caller. Failed/invalid/cancelled
  socket probes clear it and close owned sockets. `close()` releases payload and
  header references; it is not a secure-memory-erasure guarantee. Reuse rejected.
- Payloads never appear in the probe result, object representation or API; no new
  HTTP option, target selection or automatic capability registration was added.

The existing offline decoder validator now uses a structural in-memory sample
contract, allowing this collector without importing research scripts into the
driver. FFprobe and strict FFmpeg remain sequential, single-threaded, pipe-only,
null-output and capped at 512 MiB / 30 CPU seconds / 45 seconds wall time each.
No decoder executes inside the live receive loop.

## Offline evidence

Synthetic tests cover IDR waiting, audio exclusion, timestamp span, frame/byte
limits, unsupported/missing/changing headers, corruption, epoch changes, explicit
clearing and socket-probe success/failure/cancellation cleanup.

The existing private PCAP's `flow5` was replayed through this exact collector,
then independently probed and strictly decoded:

- 120 input and decoded frames, 220,158 elementary-stream bytes;
- HEVC 640×360, matching the captured encoding header;
- strict decode, dimensions and frame-count comparisons all passed;
- accepted timestamp span 7,848,000 raw ticks, zero pre-IDR frames discarded;
- sample cleared in `finally`; no media files or camera connections.

This is **historical capture** decoding, not a new live-camera decoding result.
The previous count-only live success is in
[native-av-first-success.md](native-av-first-success.md).

Full validation: 1,786 Python 3.12 tests passed with one Node-dependent skip in a
512 MiB / one-CPU disposable container. Host Node contracts, Ruff and Mypy
(188 files) passed. No app rebuild or production restart in this milestone.

## Next checkpoint

Wire a separate server-configured opt-in through the reserved same-process route
owner, with sample clearing even if B9 fails. Finish AV CLOSE/B9/socket cleanup
before any independent decoding. Decoder failures must not leak payloads or leave
retained samples. Review decoder availability/isolation in the deployment image
before enabling a bounded camera-3 sample; never run an extra persistent producer.
Only after a real decoded keyframe and coherent timestamps should dashboard
integration, source sharing and RTSP fallback be implemented.
