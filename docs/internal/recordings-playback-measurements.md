# Recording startup measurements — 2026-09-23

## Scope and safeguards

One closed camera-3 archive was selected read-only through the local index. It was
older than 15 minutes and below 100 MiB. No camera command, stream restart, registry
write or container rebuild was performed. Output is derived media in a git-ignored
temporary directory, not the production playback cache. No images/audio were
displayed or played. No absolute private archive path is recorded here.

The initial host test with a **512 MiB virtual-address-space** ceiling failed with
`pthread_create`/EAGAIN. It was not a WSL OOM. The successful repeat used an isolated
container with no network, 512 MiB RAM/no swap, one CPU, 64 PID ceiling, source/backend
read-only mounts and a temporary output mount. Sample/full conversion wall timeouts
were 25/90 seconds. The container was removed automatically; running services were
left untouched.

## Results

Source: HEVC 1920×1080 + AAC, 300.068 seconds, 2,608,673 bytes.

| Conversion | Wall time | Peak child RSS | Output |
| --- | --- | --- | --- |
| First 5 seconds | 879 ms | 159,984 KiB | H.264 1920×1080 + AAC; 5.041 s; 78,732 bytes |
| Complete archive | 22,098 ms | 165,064 KiB | H.264 1920×1080 + AAC; 300.201 s; 3,825,034 bytes |

Both use the current production command builder: one decoder/encoder/filter thread,
ultrafast H.264, copied audio and faststart MP4. ffprobe verified codec, dimensions
and duration; this is not a browser rendering or full audio-sync homologation.
The small duration difference remains visible here rather than being rounded away.
These measurements use one recording and capped resources, not a general throughput
benchmark or a comparison against the earlier unrestricted encoder.

The uncached path intentionally waits for complete conversion before attaching the
seekable file. Thus **22.1 seconds of preparation alone** is a measured cause of
first-open delay on this sample. Downloads bypass conversion. This does not prove
that all observed browser stalls have the same cause.

## Repeated metadata work removed

Code inspection also found repeated ffprobe work in preparation and original-file
requests (including Range requests). `recording/codec_cache.py` now retains at most
256 successful results, keyed by resolved path, device/inode, size, mtime_ns and
ctime_ns. It checks identity again after probing, so growing/replaced files do not
publish stale cache entries. Failures and unknown codecs are not cached. File
deletion still goes through normal HTTP path validation. No media bytes are cached
in RAM. Concurrent misses may probe independently; locks do not span subprocess I/O.

On the derived real file, sequential codec lookups measured **264.474 ms cold** and
**0.058 ms cached**. This improves repeat metadata lookup, not the initial 22.1s
video conversion. There is no persistent cache migration or new user setting.

## HTTP seek verification

Ten ASGI-level tests exercise real FileResponse handling for original and derived
files: prefix, mid-file, suffix and open-ended ranges return 206 with exact payload,
Content-Range and Content-Length; invalid range returns 416; matching/stale If-Range
produces partial/full responses; unauthenticated Range requests remain rejected.
They use deterministic small test payloads, not real production HTTP timings.
No missing Range support was found in this tested path. Existing direct route
tests alone could not establish this because they never streamed an ASGI response.

Next: deploy the reviewed changes and validate first-click/reselection/seek on a
real browser, measuring preparation separately from ready-file first byte and play.
Consider native HEVC playback only with explicit client capability detection and
tested fallback; do not blindly serve HEVC to all browsers or return a partial
conversion that breaks seeking. Full conversion waiting remains an open design
tradeoff, not a resolved startup-delay bug.

## Deployment and real HTTP check

Runtime commit `9a092c5` was built as image `299f3352c407`, dashboard build
`b-c424c1dc6a43`. Legacy Docker build was capped at 512 MiB/no swap and one CPU;
the context was 3.963 MB. Only `ccg-app` was recreated. go2rtc and unrelated
services retained their start times; both native diagnostic flags remained false.
Health returned 200 after startup (the immediate early readiness check initially
got connection refused while the application was starting).

One authenticated loopback check through the actual application selected a closed,
bounded camera-3 archive. It used the normal prepare/status/file APIs and allowed
one shared conversion, not a separate unmanaged encoder. This was a different
closed archive from the isolated benchmark; do not compare the timings as an
encoder performance improvement.

| Observation | Result |
| --- | --- |
| Initially cached | No |
| Initial status response | 122.07 ms |
| Preparation POST response | 7.07 ms |
| Ready observed (1-second polling) | 18,143 ms; 18 polls |
| Prefix 4 KiB | HTTP 206; headers in 5.76 ms |
| Mid-file 4 KiB | HTTP 206; headers in 3.24 ms |
| Suffix 4 KiB | HTTP 206; headers in 3.83 ms |
| Cached preparation POST | 3.54 ms; ready/cached, no transcode |
| Request without session cookie | HTTP 401 |

All three requested ranges contained exactly 4096 bytes and consistent Content-Range
headers for the 4,282,896-byte derived file. No media payload, cookie or key was
printed. The production derived cache gained that reproducible file; originals were
untouched. Post-check application memory was about 106 MiB; no OOM/restarts were
reported. Two observations found one producer for each of three base streams with
increasing byte counters. App recreation briefly interrupts recorder ownership;
this is not a claim of zero recording downtime.

The HTTP metrics above were measured externally. Searching container logs found
no `playback_prepare` INFO record: the current logging configuration does not
surface that logger's INFO events by default. Follow-up: wire scoped content-free
metrics logging (and verify failure visibility) without enabling verbose SDK or
credential-bearing logs globally.

No browser was launched in this deployment check. First-click autoplay, seek UX
and mobile rendering remain to be validated in a real browser; HTTP success is
not a substitute. Backend latency on this sample is concentrated in cold
conversion, not serving the ready file. Do not revert to fragmented partial
playback merely to hide that preparation delay.

## Scoped logging follow-up

Application startup now configures only `backend.app.recording.playback` at INFO
with one stderr handler and propagation disabled. Repeated startup does not add
another handler. Root, SDK and camera logger levels remain unchanged. This makes
one completion event per preparation job visible under the default Uvicorn logging
configuration, without per-frame/per-poll output or a new log file.

Events contain only outcome, a fixed reason code and queue/encode milliseconds.
Reasons distinguish queue timeout, encoder timeout, nonzero encoder exit, missing
output, I/O/process error and cleanup failure; successful cache reuse is distinct
from encoding. Exception text, command lines, paths, camera identifiers, media and
credentials are not included. Admission rejections before a job exists still use
HTTP 429; these metrics do not claim to cover those rejections or browser latency.

Validation: 35 focused tests passed using fake encoders, including eight completion
outcomes and an isolated subprocess using Uvicorn's actual default logging config.
The subprocess confirms INFO visibility, a single event after repeated setup and
no enabling of unrelated SDK INFO. Ruff and Mypy (193 files) passed. No camera
commands, conversion load or running-container restart was needed. Deployment of
this backend-only logging follow-up remains pending; it takes effect on the next
application image update/startup, not through the live-mounted frontend.
