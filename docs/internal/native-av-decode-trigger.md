# Reserved native-video decode diagnostic — 2026-09-22

Server-only `NATIVE_AV_DIAGNOSTIC_DECODE_VIDEO=false` is an additional opt-in on
the existing one-shot, authenticated, direct-loopback native AV diagnostic. It
does not enable the endpoint by itself. No query/body override or public driver
capability was added. With the flag off the existing count-only result is unchanged.

## Execution order and lifetime

1. Existing target identity, enrollment, per-camera exclusion and one-attempt gates.
2. Check `prlimit`, `ffprobe`, `ffmpeg` availability before touching the camera.
3. One fixed three-second route sample through `AvVideoSample`: 2 MiB / 120 frames,
   configured-IDR start, HEVC up to 1920×1080, no retained audio or media files.
4. AV CLOSE receipt, B9 release receipt, local socket close. The route wrapper
   clears the sample on **any** exception, including preflight, B9 or socket-close
   failure. Only a successfully closed route hands retained data to the decoder.
5. Independent FFprobe then strict FFmpeg, sequentially. Each child has 512 MiB
   address-space, 10 CPU-second, 64-descriptor and 15-second wall-time limits.
   Threads/filter threads limited to one, only `pipe` input protocol, no shell,
   null output and discarded stderr. No decoder runs while receiving camera UDP.
6. Require HEVC, dimensions and decoded frame count to match the sample, plus a
   successful strict decode. Return a fixed metadata dataclass only.
7. Clear payload/header references in `finally` after success or any failure.
   Keep the application camera lock through decoding/clearing.

Cancellation is checked before/between/after children; it does not interrupt a
child mid-decode (each has the 15s deadline). The existing HTTP disconnect also
does not cancel this bounded synchronous diagnostic. Allow up to the existing
route bound plus 30s decoder wall time in the operator client; never retry an
uncertain HTTP result. One server worker and the process-wide single-use gate
remain required; do not deploy multiple diagnostic replicas/workers.

In decode mode success has `route` (the former route result) and `video` (frames,
dimensions, input bytes, relative timestamp span, pre-IDR discard count). Neither
includes payloads, absolute timestamps, endpoints or credentials. Decode errors
remain sanitized HTTP 502 failures, not partial success. Route-success telemetry
alone does not prove subsequent decoding succeeded: check the HTTP result.

## Validation and deployment boundary

Focused tests cover server-only opt-in, exact subprocess limits, absent tools,
malformed/oversize probe output, codec/dimension/frame mismatch, strict-decode
failure, timeout/cancellation, route-before-decoder ordering and sample clearing
after B9/preflight/decoder errors. Existing count-only contracts are preserved.

The new runtime decoder was exercised offline against the private PCAP through
the bounded collector: **120 decoded HEVC 640×360 frames**, 220,158 input bytes,
7,848,000 relative timestamp ticks, no pre-IDR discards. Sample cleared. This is
not a new live decode result. The currently deployed app was checked read-only:
all three required executables exist at `/usr/bin/`; no package installation or
container restart was needed for that check.

Full local validation: 1,806 Python 3.12 tests passed with one Node-dependent skip
in a disposable 512 MiB / one-CPU container. Host Node contracts, Ruff and Mypy
(189 files) passed.

The integration is not yet deployed or live-enabled. After green CI, build with
the existing 512 MiB/one-CPU cap and make one reviewed camera-3 attempt. Reset both
diagnostic flags and target fields in the ignored override before invoking; restore
runtime disabled/empty after collecting safe results. Check ordinary RTSP ingress.
No audio playback, speaker transmission, lights or movement are part of this test.

Related: [sample collector](native-av-video-sample.md),
[first native reception](native-av-first-success.md),
[operator gates](native-av-operator-trigger.md).
