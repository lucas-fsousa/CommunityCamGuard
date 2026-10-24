# Native shared-source WebSocket delivery — 2026-10-23

Status: implemented and tested with isolated ASGI clients and synthetic video.
Production source scheduling and safe source switching remain pending. No live
camera session, production deployment or browser rendering test was performed.

The explicit WebSocket route-auth inventory reviews this endpoint's delegation
to `serve_native_video`, including its `live` grant, origin check and continuing
session guard. The first CI run caught the missing inventory entry; the reviewed
gate is now included rather than exempting new WebSocket wrappers generally.

## Delivery contract

`/api/media/native/{camera_id}/ws` accepts only an opaque configured camera ID,
the normal session cookie, an allowed dashboard origin and the `live` permission.
Query parameters, arbitrary source URLs and WebRTC negotiation are rejected.
Authorization and camera existence are checked before accepting the socket and
rechecked by the existing session watchdog. Revocation/expiration removes that
reader without stopping independent readers or the source.

The server must explicitly provide `app.state.native_sources` as a `SourceRegistry`.
The route never constructs a registry, starts a source, authenticates a camera or
creates an encoder. Startup now provides an initially empty registry owned by the
[native runtime lifecycle](native-runtime-lifecycle.md), so the dedicated native
route declines unclaimed IDs with code1013 before accepting. No source is scheduled
or restored. This is essential while the
[existing RTSP ingress retirement](go2rtc-source-retirement.md) is unresolved.

The existing dashboard URL `/api/go2rtc/ws?src={camera_id}_hd` (also `_web`/base)
now routes to this same adapter when that public ID is reserved in the registry.
Both primary and temporary sessions go through the native permission check.
Starting/stopping/blocked entries remain reserved: an unavailable native source
returns1013 without opening the legacy upstream. This router does not stop an
already-open RTSP source or perform the missing production handoff itself.

`/api/media/streams` includes `native_sources`, so the dashboard hides its quality
selector for this currently single-quality native output. The existing aliases
still address that one full-resolution output. `/media/activity` excludes legacy
counters for reserved native IDs, and `/media/recover/{id}` only acknowledges the
client's reconnect without touching legacy producers or restarting the shared
source. The manifest is loaded at session boot; live manifest/source migration is
not implemented. No new user-facing source selector is enabled.

After acceptance, the client has10s to send the same bounded MSE request shape as
VideoRTC, for example `{"type":"mse","value":"avc1.640029,mp4a.40.2"}`. H264
must be advertised; the current baseline-only output must fit the maximum declared
H264 level. This is a video-only path; advertised audio is not synthesized.
No further messages are permitted except disconnect.

The server responds with `{"type":"mse","value":"video/mp4; codecs=\"avc1.…\""}`
then binary initialization and complete fragment messages. The exact baseline
profile/compatibility/level bytes are read from the locally generated MP4 `avcC`
record, through its bounded box hierarchy. They are not guessed from resolution
or found by searching arbitrary payload bytes. This helper is not a general
untrusted MP4 semantic validator. The existing player can use its normal socket
alias; real browser capability/latency acceptance remains necessary.

## Resource ownership

- One source and encoder remain shared. Each client gets one fanout cursor.
- Each transport has at most one pending media write, referencing existing bytes;
  it does not copy media into an additional queue. ASGI/OS transport buffers are
  outside the fanout's accounting.
- Acceptance and each write have a2s deadline. An idle source has a10s deadline,
  with20ms asynchronous polling rather than a thread per viewer.
- A disconnect, protocol error, expired permission, retired epoch, slow reader or
  failed write closes the subscriber and reaps its receive/send tasks. It never
  calls `SourceRegistry.stop` or opens fallback.
- All socket closing uses the bounded shared helper. The limits assume normal
  cooperative ASGI cancellation; they are not proof of physical camera teardown.

Close codes:1008 for access/protocol rejection;1013 for unconfigured/unready source
or ended/evicted epoch;1011 for transport/idle/internal delivery failure. No private
media or upstream credentials are included in error messages or logs.

## Evidence

ASGI tests exercise two viewers on the same admitted source, extra-negotiation
rejection without affecting the other viewer, missing scheduler, unauthorized
origins/cookies/grants/IDs/query parameters, malformed/duplicate negotiation,
revocation, exact expired-key rejection, camera removal, epoch retirement and a
stalled write. The stalled-write check verifies no leaked asyncio tasks and that
an independent subscriber still reads the original bytes.
The existing player aliases also pass with primary and temporary sessions while a
legacy-upstream factory is forbidden. Delayed and uncertain native cleanup return
1013 without fallback. Manifest/activity/recovery checks retain the source owner.

The isolated real FFmpeg check generated64×64 HEVC, transcoded it through one
ongoing encoder and delivered the same3748 bytes to two fanout readers. ffprobe
decoded125 H264 frames and agreed with the parsed level10/`avc1.42C00A` metadata.
The scope peaked at107.7MiB, with zero swap. This synthetic result does not certify
1080p resource use, real-camera endurance, audio delivery or browser playback.
