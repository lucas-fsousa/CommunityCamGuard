# Broker media route on the shared account reader

## Bounded video consumer handoff

The worker now accepts optional internal `video_delivery=VideoDelivery()` alongside
the explicit TCP bootstrap. The existing sole TCP reader feeds this handoff; no
extra camera connection, authentication, thread or decoder process is created.
One consumer polls `take()` for immutable video frames (payload omitted from repr),
raw camera timestamps, dimensions and decoder-restart epoch. Audio is never retained.
The handoff is not yet instantiated by the production service or dashboard.

The queue is capped at2MiB/64frames, with a fixed HEVC <=1080p admission profile.
No waiting for consumer capacity: overflow clears backlog and resets the HEVC gate.
Only a fresh complete access unit containing VPS/SPS/PPS and IDR resumes delivery.
Malformed frames, timestamp regression and changed dimensions/configuration cannot
carry old dependent frames into a new decoder epoch. Slow-consumer loss is intentional;
this is a live-view handoff, not an archival recording guarantee. Limits cover queue
references, not data retained independently by a consumer or the TCP parser.

The lock guards bounded local work only (no callbacks/I/O). Source shutdown,
cancellation and failure retire the queue and clear frame references; normal finite
completion also discards unread tail. A future decoder must consume while active,
respect restart_decoder and close its own resources. This queue does not itself
extend the finite TCP session lifetime, provide multi-consumer fan-out, decode,
remux, resample or expose native media over HTTP. Those integration steps remain.

44 focused offline tests passed with124.4MiB peak/zero swap. Protocol fixtures verify
handoff invocation in the cooperative reader and queue retirement before legacy
account release; tests cover overload by bytes/count, IDR recovery, malformed header,
oversized payload, timestamp regression and terminal close. Ruff/mypy passed.
No live camera command, build/restart or production-source switch in this step.

## Physical acceptance — 2026-10-21

The opt-in, single-use `/api/internal/diagnostics/native-av` route now supports
server-only `NATIVE_AV_DIAGNOSTIC_SHARED_TCP=true`, requiring TCP relay, short
profile and no decoding/platform-only flags. Existing local-primary-auth boundary
and fixed reviewed target remain mandatory; default is false. No public control
or capability was enabled. `yoosee_shared_media.py` reserves the camera operation
and quiesces the legacy account cache through cleanup, authenticates once, and
binds media to the actual returned online target (now retained by ReadConnection).
Unknown TCP/B9/socket cleanup poisons the legacy account reservation.

An authorized camera3 run returned HTTP200/media_complete:39 video records,
62 audio records,1 encoding header,13 sent/10 received broker datagrams.
AVCLOSE and correlated B9 were confirmed, both physical sockets closed without
error. No heartbeat occurred in this short window; it is not evidence of prolonged
stability, audible quality or decoded display. Records were counted/discarded.
No microphone output, light, motion or reset command was sent.

First attempt returned409 (no media evidence); startup PTZ preparation was a
possible contention source. The successful deployment explicitly disabled
background preparation during the one-shot experiment. Afterward it restored
preparation, disabled diagnostic flags and cleared targets. No automatic retry.
Image020039f4751d built with512MiB/no-swap/half-CPU limit; only app recreated.
The normal RTSP/go2rtc source was not changed.

The pump now waits at most1ms for UDP while TCP is attached; the former50ms wait
could cap4096-byte TCP steps around80KiB/s. Idle non-media behavior is unchanged.
Focused adapter/API/connection/TCP tests:52 passed; pump regression subset37 passed.
Peak test memory102MiB, zero swap. Ruff and targeted mypy passed.

Next: bounded consumer delivery and continuous media lifetime, then source/fallback
scheduling. Do not advertise production native playback from this finite test.

2026-10-20 — offline-tested, no production caller, camera traffic or deployment.

The account worker now accepts a trusted, typed `initial_media` bootstrap. It
allocates A4 OPEN and B9 CLOSE from the same sequence namespace as identity reads
and heartbeats, before emitting either packet. One route per generation is allowed
in this first integration. It does not authenticate or create another UDP socket.
`MediaRouteSpec` is not a capability grant: eventual driver admission must bind it
to the exact enrollment and authenticated online-device evidence.

`MediaRouteJob` owns broker negotiation only. It selects a checksum/session/link-
correlated global IPv4 TCP relay using the same extracted policy as the existing
TCP diagnostic. It retains one endpoint, not an advertisement queue. OPEN has a
one-second admission window and a four-second relay deadline. Once emitted, an
OPEN is never replayed, including partial/failed sends and interruption.

Cleanup capacity is reserved before OPEN: one OPEN and one CLOSE packet, separately
from PTZ's six-slot reserve. Normal reads/ACKs cannot borrow either reserve, even
across continuous-policy refill. CLOSE uses its preallocated sequence after account
retirement, waits at most1.5seconds for an exact positive B9 receipt, and is emitted
once. Wrong session/sequence/type/generation/peer, malformed/late acknowledgements
or negative results cannot count as successful release. Unknown release retains
the poisoned route reservation and blocks lifecycle generation replacement.

The existing sole reader handles negotiation during normal work; on shutdown it
uses the same socket for bounded CLOSE draining before physical close. No extra
reader, thread, socket, login or retry loop. The cleanup pump has a64-datagram input
ceiling and retains no history. Completed jobs clear node/cookie/wire references.

## Evidence

183 focused tests passed in7.498s,93.5MiB peak/zero swap, with192MiB/no-swap/half-CPU
limits. Ruff and mypy passed. Encrypted synthetic peers prove one socket serves
OPEN, identity read, heartbeat and CLOSE with four different sequences. Tests cover
ambiguous OPEN/CLOSE, missing relay/receipt, negative receipt, interruption,
pre-start cancellation, sequence exhaustion, strict receipt correlation and
generation replacement denial. Existing finite TCP diagnostic and PTZ tests pass.

## Explicit limits and next integration

- The initial broker-only step did not attach a TCP channel. The2026-10-21 follow-up
  below adds finite TCP reception/AVCLOSE, but no decoder/dashboard delivery.
  A positive broker B9 remains a transport receipt, **not** proof of camera resource
  reclamation or media-channel shutdown.
- The bootstrap currently rejects combining media with motion admission. Reservation
  coexistence is tested, but concurrent PTZ/video operation is not certified.
- This first route is single-use per generation. Multi-camera route admission still
  needs bounded per-device ownership and validated online-device binding.
- Next add bounded consumer delivery, continuous-media lifetime and server source
  scheduling/fallback, then validate on camera3. Do not activate production from
  these offline results alone.

No sound/light/other physical command was executed. Existing RTSP remains unchanged.

## Cooperative TCP attachment — 2026-10-21

Explicit internal `with_media_tcp=True` now attaches `CooperativeTcpMedia` after
the broker reader receives the correlated relay advertisement. Default remains
false; no production service, public setting or endpoint enables it. The TCP
factory connects once (500ms socket timeout), sends one pairing request (200ms),
and transfers exclusive ownership; failed opening closes its socket. Unconfirmed
physical close raises beyond ordinary transport failure so replacement is blocked.

The account loop drives one4096-byte TCP receive per iteration, with1ms read and
20ms write timeouts and an overall200ms per-step I/O budget. Parsing remains
bounded by the existing framer/AV limits; these are not OS scheduling guarantees.
TCP never reads UDP and creates no thread or authentication. Records are counted
and discarded, not queued, decoded, played or delivered to a browser. Existing
SHORT measurement/observation/time/traffic limits remain: this is finite media
reception on the shared reader, not yet continuous video.

Normal completion and cancellation drain AVCLOSE for up to2seconds, physically
close TCP, then request broker B9 and close UDP. Missing AVCLOSE receipt or failed
TCP close blocks lifecycle replacement even when B9 succeeds. Early negotiation
failure cannot fabricate an AVCLOSE receipt. TCP cleanup interruption still lets
the worker attempt broker cleanup before propagating the failure. No reconnection,
pairing retry or automatic fallback is introduced.

141 focused tests passed in6.967s,90.5MiB peak/zero swap; Ruff/mypy passed. Real
protocol fixtures exercise measurement, fragmented AV negotiation, parsed video/
audio records and AVCLOSE while the same worker services encrypted identity reads.
Normal and cancelled paths verify TCP physical close before B9; additional cases
cover connect/pair failure, EOF, bad peer, missing AVCLOSE, uncertain physical close,
and generation denial. No real network, camera command, build or restart was used.
