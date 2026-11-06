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
