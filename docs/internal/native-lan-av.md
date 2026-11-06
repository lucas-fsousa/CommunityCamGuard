# LAN AV selection on the shared account reader

## Implemented, awaiting the camera3 live checkpoint

This follows the first live LAN roundtrip documented in
[LAN selection evidence](native-lan-selection.md). It reuses the existing
AvHandshake/AvReceiver/KCP/V1 engine, not a second protocol implementation.

- Leased sources measure the authenticated broker-provided LAN candidate before
  constructing a media transport. The probe is cooperative and expires in1.5s.
- A correlated roundtrip selects one UDP AV owner. No TCP media connection is
  opened for that call. Missing/silent LAN measurement selects the existing TCP
  relay path. Non-leased historical diagnostics retain their existing transport.
- The account's sole recvfrom routes exact-peer, valid-MTP, exact-conversation
  packets into that AV owner. Other datagrams still traverse the broker's foreign
  input budget. Valid media uses the existing per-stream constant-space rate
  budget instead of exhausting the broker/control allowance.
- The existing bounded delivery/decoder/browser/archive consumers remain
  unchanged. Audio here is camera-to-server reception, NOT microphone/speaker
  output or a siren command.
- Meters are maintained on the same UDP socket. Continuous replies are
  rate-limited, not stopped by the old finite64-reply diagnostic limit.
- UDP negotiation or receive failure detaches its AV parser and requests the
  normal broker release. Only confirmed retirement permits another lease. The
  same account then selects TCP for that device; no competing login or automatic
  replay of camera actions occurs. UDP is not retried on that account generation.
- UDP retirement reports local detachment and its actual CLOSE receipt
  separately. It never claims that the shared broker socket or a nonexistent TCP
  media socket was physically closed. Missing outer release still fences cleanup.
- Normal cancellation pumps CLOSE in reader turns. Final account shutdown sends
  a best-effort CLOSE without nested recvfrom, detaches, then obtains broker
  release through the existing cleanup path.

## Offline acceptance

Synthetic checks exercise actual LAN INIT/ACCEPT/START/media/CLOSE messages,
delivery of an HEVC IDR through the existing handoff, no simultaneous TCP opener,
and a failed UDP negotiation followed by a new TCP lease on the SAME broker
reader with old-route release. Wrong peer/conversation/checksum cannot obtain
the media-budget bypass. Existing finite meter limits are retained for their
original callers. Tests/builds remain memory/CPU capped; no full local suite.

Do not label live UDP AV or freeze resolution accepted from these tests alone.
The live checkpoint must show the UDP-ready log, advancing browser media and
recording progress for camera3, with the two field cameras still legacy RTSP.

## First live attempt and captured envelope mismatch

Commit38c68f3 passed CI42295083521 and Image42295083447, then ran as capped
image29acec2a4c9d. Camera3 passed its LAN meter but did not reach AV readiness.
The UDP owner detached, obtained B9 release, and the SAME account recovered on
TCP automatically. Its event honestly reported udp_detached=true,
tcp_socket_closed=false; there was no permanent quarantine.

A60s header-only capture saw38 c0/90 packets and THREE c0/18 packets of30bytes,
not c0/10. The canonical KCP parser accepted only c0/10 and therefore discarded
those replies. No payload was retained; treat their ACK interpretation as a
candidate until exact-conversation/sequence/timestamp validation accepts them.

Split ELF510bc51a confirms the receiving rule: UDP dispatcher0x21a4cc-0x21a524
tests bit7 (control) and bit4 (data), not opaque low bit3. Handler
iv_on_rcv_kcpdata_pkt0x25f448-0x25f478 uses a six-byte prefix for mode bits5/6
equal to zero, then forwards unchanged KCP bytes. The isolated UDP normalizer
now validates the ORIGINAL envelope/checksum before mapping only observed c0/18
to canonical c0/10. Other modes, invalid checksum and foreign conversations are
still rejected. TCP framing and the common KCP engine remain unchanged.

Synthetic negotiation covers both observed envelope forms and rejects damaged
or relay-mode input. Per-channel retirement now preserves known local error
codes rather than replacing every initial UDP error with an opaque fallback
label; only unknown initial negotiation failures use udp_negotiation_failed.

Also corrected an omitted SDK step before the second live attempt: the existing
successful finite UDP bootstrap sends direct mode1 A4 CALLING as well as the
broker A4. The SDK queues both at0x2506cc-0x250704. The earlier note established
only that the direct A4 RECEIPT was not a readiness prerequisite; it did not
establish that the request itself could be omitted. The LAN owner now sends that
existing builder exactly once before INIT, preserving the same cookie, call ID,
definition metadata and fresh account-allocated sequence. It does not require an
A4 receipt or create another broker OPEN/login. Synthetic worker reception still
omits the A4 receipt and negotiates successfully; duplicate direct attempts are
not admitted on one route.

## Follow-up: reproduce the existing complete UDP rendezvous

The next live attempt (85c6e5e) recognized a real high-conversation sequence0
KCP ACK, but ended with av_handshake_timeout and recovered on TCP. The passive
capture saw no ACCEPT/START. Current camera meters have the existing accepted
kind1/channel4/role2/68-byte-body profile, so their role is not a demonstrated
cause. The old capture's AV ACCEPT/START actually arrive over LAN; it does not
prove that AV control must use the NAT endpoint.

An actual omission versus the previously successful finite UDP primitive is
the CA/CB rendezvous: call_device explicitly performs it before AV admission.
The shared reader now reproduces that bounded exchange with the exact public
peer from authenticated A3, alongside LAN measurement. No extra login, recvfrom
or independent AV owner is introduced. Unknown/late peers, wrong link and bad
checksum cannot establish the exchange. Queued receipts are discarded if their
route is cancelled. Failure retains TCP instead of sending AV prematurely.

The UDP owner can receive the same logical conversation through either its
measured LAN endpoint or independently proved NAT endpoint, matching the SDK's
conversation-based receive dispatch. This is explicit route aliasing AFTER
endpoint/wire/conversation validation, not acceptance of arbitrary senders.
ACKs return to the actual incoming path. SDK meter channel3 (NAT) and4 (LAN)
are supported there; the reply copies the received channel as iv_rcv_meter_req
does. Existing finite callers retain their channel4 admission by default.

This restores known protocol steps; it is not yet evidence of live AV success.

## Correct the actual SDK UDP meter ACK, keep NAT optional for LAN

The7d23976 checkpoint passed CI42299002113 and ran as image3369612190ae.
LAN measurement succeeded but the public CA/CB exchange did not complete;
TCP remained available. That negative NAT result must NOT invalidate the
independently measured LAN channel. The SDK optimizer can select one positive
channel. Public traversal now runs alongside LAN, without gating LAN INIT;
only a NAT peer that actually proves the exact route is accepted as an alias.

Pinned split ELF510bc51a, iv_rcv_meter_req0x25cfc0-0x25d048, reveals a concrete
ACK-format bug: it writes reply marker2 at body+0x40 (byte64), not the REQUEST
role field at byte65. Our helper wrote byte65. It also copied only the low32
timestamp bits, whereas the SDK copies all64 at0x25d014-0x25d020. The UDP helper
now echoes the complete clock and emits tail02/00/00/00, matching the same
mapped reply profile already recognized by the bootstrap validator.

A passive current-camera sample confirmed request source/destination match the
enrolled device/account and role2 is already accepted. Its high timestamp bits
were zero, so timestamp truncation was NOT demonstrated as this attempt's cause.
The older binary's zero-tail note is not a substitute for the pinned split's
explicit marker write. No role wildcard, guessed port or larger timeout was added.
