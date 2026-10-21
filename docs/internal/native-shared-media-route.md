# Broker media route on the shared account reader

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

- No media TCP socket, pairing, AVCLOSE, decoding or dashboard delivery is attached
  to this worker yet. A positive broker B9 is a transport receipt, **not** proof of
  camera resource reclamation or media-channel shutdown.
- The bootstrap currently rejects combining media with motion admission. Reservation
  coexistence is tested, but concurrent PTZ/video operation is not certified.
- This first route is single-use per generation. Multi-camera route admission still
  needs bounded per-device ownership and validated online-device binding.
- Next attach the existing TCP/AV lifecycle without letting its receive loop read
  the broker socket. Media AVCLOSE/TCP shutdown must precede broker route CLOSE;
  consumer events and buffers must remain bounded. Then integrate the server source
  scheduler/fallback and validate on camera3. Do not activate production from these
  offline results alone.

No sound/light/other physical command was executed. Existing RTSP remains unchanged.
