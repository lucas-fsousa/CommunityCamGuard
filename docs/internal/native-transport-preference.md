# Native transport preference with standard fallback

Status: staged implementation, 2026-09-12. Production PTZ remains ONVIF and video remains RTSP.
The user requests proven native transports as preferred paths, with standards retained as fallback.

## Bounded ownership and prepared route increment

`p2p/ptz_motion.py` now owns one non-renewable 100–500 ms gesture, with thread-safe early STOP,
single-use/reentrancy protection and no START acknowledgement wait before the RELEASE deadline.
An attempted START is recorded before the socket call: even a send exception forbids movement
fallback. Cancellation before START closes the route without movement or fallback. Cleanup tries
only RELEASE, up to three times within a separate two-second budget, then closes the route.
Results distinguish delivery confirmation from physical motor state. Nonblocking/deadline behavior
is part of the adapter contract; process death or network loss cannot guarantee physical STOP.

`PtzOwners` provides bounded no-queue reservations (four by default, at most eight), including
same-camera exclusion, to acquire before establishing any route. It does not spawn worker threads.
The future session service must hold a reservation through preparation, movement and cleanup.

`p2p/ptz_route.py` implements the prepared UDP adapter: fixed node/device/direction and immutable
START/RELEASE packets, nonblocking sends, identical RELEASE retransmission, no reconnect and
idempotent socket close. Receipt collection is bounded by deadline, 64 packets and 4 KiB frames;
checks include peer, encrypted node session and release-specific correlation. START receipts do
not confirm RELEASE. An explicit application error overrides prior success within the bounded
observation window. Strict reliable-queue rewriting compatibility remains unproven live.

59 focused protocol/owner/route tests passed with fake clocks and synthetic sockets, covering
early cancellation, ambiguous START, failed RELEASE, bounded retry, close failure, duplicate
ownership, stale receipts and explicit error precedence. No camera movement or container update
was needed to establish these boundaries. These modules are not wired into driver/API routes.
Next: exact-unit route preparation and live RELEASE-only correlation check, followed by a short
camera-3-only gesture with cleanup. Continuous browser gestures require a separate lease/heartbeat
contract; do not redirect the current 450 ms ONVIF repeat loop to this adapter.

## Independent driver decisions

Transport preference is per device/model/firmware **and feature**, never global brand selection.
Native PTZ availability does not authorize native media or talkback. Generic cameras keep their
standard paths. The dashboard continues using opaque camera IDs and semantic commands; protocol,
credentials, route selection and lifecycle belong to drivers/backend.

### PTZ first

Current units implement fixed approximately 400 ms ONVIF moves and ignore STOP (ADR 0007).
The current frontend repeats START every 450 ms. Moving that repeat loop unchanged to native PTZ
would be unsafe and would not reproduce the app's continuous press/release gesture.

Required native session owner:

- Exact-unit operation proof plus fresh axis evidence before START; bounded one-session-per-camera
  ownership, no queued motion backlog and no session handshake on every repeated UI event.
- One START, explicit RELEASE on pointer release/cancel, and a server-side lease/watchdog even if
  the browser disconnects. The STOP deadline cannot wait behind a lost START acknowledgement.
- START/STOP pinned to the same chosen transport. Standard fallback is allowed only when no native
  START could have been sent. A timeout after attempted START is ambiguous: clean up that native
  route and report failure, never retry movement via ONVIF or another route.
- Keep the existing finite-step ONVIF interaction as fallback, with driver-advertised interaction
  semantics so the frontend does not guess by brand. Validate contention, disconnect, delayed
  replies, release scheduling and cleanup before opt-in.

First implementation increment: `p2p/ptz_protocol.py` contains only the fixed type-2 encrypted
press/release encoder, exact `ProReadonly.devInfo.stVal.ptzInfo.id0_status` axis interpretation,
and bounded reply decoding. It has no socket or generic JSON sender and cannot start movement.
It distinguishes node delivery receipt, camera-side receipt and explicit application error;
none is proof the motor stopped. The frame sender must independently check UDP peer/decryption.
Other heads, absent/invalid timestamps, boolean states and another axis do not grant movement.

The older ignored RE harness tolerated rewritten request/sequence IDs in the reliable queue.
The strict codec intentionally does not accept same-type replies merely because only one request
is expected. Fresh native capture must establish reliable-queue correlation before rollout;
this stricter codec is not yet claimed compatible with every observed SDK packet variant.
B9 replies correlate their embedded request ID; their own reliable message ID need not equal
the outbound message ID. BA receipts correlate the outbound message ID.

Read-only camera-3 validation at 2026-09-12T04:23:43Z: one bounded session read both ProConst
identity roots and `ProReadonly.devInfo` using correlated B7. The complete identity matched the
reviewed unit and all four directions passed the exact axis parser. No START/STOP, sound, light,
stream or other camera action. Ignored reproduction: `re/verify_camera3_native_ptz_read.py`.
This is current capability evidence, not a new movement/latency or cleanup proof.

### Native video second

MTP/KCP session acceptance and media reception are already known. Remaining work is bounded
continuous frame assembly, codec/config/keyframe boundaries, timestamps, audio/video sync,
loss/reconnect handling and a local restream source compatible with the existing consumers.
Do not equate bulk packet reception with a production live-view/recording source.

Compare native versus RTSP on camera 3 with the same codec/resolution/FPS: capture-to-screen delay,
stall duration, keyframe recovery, CPU/RSS and recordings continuity. RTSP alone does not impose
a low-resolution ceiling; native delivery may carry the same HEVC stream and retain browser
transcoding cost. Broker/cloud routing can also be slower or WAN-dependent.

Preserve one selected camera media producer feeding local fan-out and recording, not one session
per browser. A bounded source handoff must close the failed producer, recover on a valid keyframe,
and avoid permanent parallel native/RTSP capture or oscillating retries. Separate capability
failure from transient transport failure; expose content-free route/latency/fallback diagnostics.
Do not replace healthy production RTSP or claim LAN-only until those measurements pass.

## Validation and next step

32 socket-free codec tests cover direction/press types, bitfields, encrypted wire payload,
session/device/request mismatch, delivery versus application response, truncation and bounds.
Next: implement and test the bounded native PTZ session owner, establish live receipt correlation,
then homologate a short camera-3-only gesture and STOP recovery before making it preferred.
No container restart or production driver switch in this increment.
