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
