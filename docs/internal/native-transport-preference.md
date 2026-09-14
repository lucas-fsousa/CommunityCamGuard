# Native transport preference with standard fallback

Status: staged implementation. Video remains RTSP; native PTZ has an exact-unit finite-step opt-in.
The user requests proven native transports as preferred paths, with standards retained as fallback.

## Four-direction camera-3 rollout — 2026-09-14

Supersedes the right-only rollout described in the historical sections below. The
reported asymmetry was real: only `right` belonged to the camera-3 native profile;
the other buttons still selected finite ONVIF movement. The cache was also keyed by
direction, forcing another session setup when switching arrows.

The reviewed profile for **camera 3 only** now contains left/right/up/down. A fresh
exact-identity/axis preflight and RELEASE-only sequence confirmed delivery in all
four directions before bounded movement testing. No other unit's profile changed.
The driver continues to decide support/transport per camera, not by dashboard brand
assumptions or a default enabled for every Yoosee model.

Stopped-session reuse now keys camera + exact identity + reviewed directions +
current credentials. Preparation intersects the reviewed directions with the fresh
axis evidence; renewal rejects any other direction. Each renewed gesture has a new
START/RELEASE pair and fresh request identities, after the previous RELEASE was
confirmed. The 8s idle/20s absolute expiry, bounded cache, per-camera ownership and
200ms movement lease remain unchanged. No simultaneous motion or START retries.

Build `b-2a919087dad5`: four authenticated dashboard API steps, one each in order
left/right/up/down, all HTTP 200. Native logs (not just HTTP) confirmed START and
RELEASE delivery, `errors=none`, no ONVIF fallback:

| Direction | Session reused | Prepare | Native total | HTTP total |
|---|---|---|---|---|
| Left | no | 1958 ms | 2320 ms | 2336 ms |
| Right | yes | 1 ms | 403 ms | 411 ms |
| Up | yes | 14 ms | 383 ms | 391 ms |
| Down | yes | 2 ms | 400 ms | 405 ms |

These timings include the finite movement and STOP confirmation, not measured
motor start latency. A first action after session expiry still pays setup cost.
User visual confirmation of new-direction fluidity and physical stopping remains
pending; protocol receipts cannot prove motor behavior. No lamp/sound/reset tests.
Full Python suite, Ruff, mypy and focused cross-direction regression tests passed.

## Short stopped-session reuse and input latency — 2026-09-14

The initial finite-step integration rebuilt the session and three correlated preflight reads
on every click. The UI disabled all arrows during that wait, swallowing additional clicks before
HTTP. Retained HTTP logs showed success responses but no durations; they alone could not prove
movement or quantify that setup cost. Native diagnostics now log preparation/total milliseconds,
reuse and release result without payloads or credentials.

`p2p/ptz_cache.py` retains only a clean, confirmed-stopped route: maximum four idle sockets,
8-second idle expiry and 20-second absolute age including preparation. No keepalive, background
movement or unbounded session pool. Keys include exact camera/direction/profile/current access
credentials. Failed or unconfirmed gestures close rather than cache the route. Cancellation
before movement also closes it. After expiry or credential change, preparation runs again;
identity/axis evidence is reused only within this short absolute window, not indefinitely.
Timers release idle sockets; checkout also enforces expiry if timer execution was delayed.

Renewal transfers exclusive socket ownership to a new single-use gesture with new request IDs
and non-overlapping sequences. Stale replies cannot confirm another gesture. After confirmed
delivery, the adapter drains immediately arriving replies with a 20ms quiet wait instead of
waiting an extra 500ms on every success. Explicit errors observed during that window still win;
this does not prove that no later packet could contain an error or that the motor physically stopped.

Live camera-3 RELEASE-only comparison (no START): cold preparation **2671ms**, total **2936ms**;
reused preparation **1ms**, total **203ms**, both delivery-confirmed. These are protocol timings,
not a click-to-motion or video-latency benchmark. Reproduction and sanitized evidence are ignored:
`re/verify_camera3_ptz_cached_release.py`, `temp/camera3-ptz-cache-release-20260914.json`.
The first click after idle expiry can still wait for preparation. Native right-only scope remains.

Frontend now uses a directional pad with pointer capture, ordinary/keyboard clicks and active
drag direction tracking. A center dead zone pauses repetition; release, capture loss, blur, hidden
document and an 8-second cap stop repeats. Drag release while a request is in flight also sends
STOP to cancel/shorten native work. Requests are serialized and rate-limited; there is no backlog
of pointer positions. A busy ordinary click explicitly reports that it was not queued instead of
silently disappearing. The already committed step remains bounded by the server; a STOP request
can race with network delivery and is not a universal guarantee of zero movement after release.

This is repeated finite steps while dragging, **not** the vendor app's continuous native gesture.
Other directions still use ONVIF; no support was inferred or granted. Physical fluidity and
mobile rendering await operator validation; no browser/emulator or movement test was run here.

Published build `b-a558a3408d30`. App started normally at 2026-09-14T12:50:37Z; compose update
used `--no-deps --no-build app`. Both app/go2rtc reported running and OOMKilled=false. Build
limited to 512 MiB and one CPU. Full Python suite, separate Node DOM tests, ruff/mypy passed;
additional cache tests cover non-renewable absolute expiry and late timer ownership. No new
native direction was activated and no motion was replayed during deployment.

## Dashboard finite-step integration

The user physically confirmed the single camera-3 rightward 200ms gesture and its prompt STOP
(test started at 2026-09-14T02:50:11Z). This confirms **right only**, not every axis or model.

`native_ptz_policy.py` stores an internal per-camera opt-in with the full reviewed identity and
proven directions. No public activation API or brand-wide defaults. Initial rollout is camera 3,
right only. `native_ptz.py` owns one non-renewable 200ms gesture after fresh identity/axis preflight,
retains the same route for RELEASE and rejects concurrent native jobs (at most four globally).
API movement dispatch also shares the existing camera control/audio lock and fails busy rather
than queuing. STOP bypasses that lock to cancel preparation or shorten the active native lease.
These in-memory locks assume the current single-process application, not a multi-worker cluster.

Preparation has a 12-second budget. If it fails with a protocol/preflight error before START,
an uncancelled operation can use one standard finite ONVIF step. Missing/mismatched enrollment
is rejected for review. After attempted native START, uncertainty/failed RELEASE is an error:
no ONVIF retry, reconnect or repeated START. Process death/network loss cannot guarantee physical
STOP; the server timer is not a hardware watchdog. Cancellation of a committed standard fallback
does not shorten its existing finite pulse.

Generic driver method `ptz_interaction` tells the browser `step` or existing `hold`. No vendor IDs,
protocol choice or firmware guessing in the frontend. For opted-in camera 3 the UI sends one
`step` per click, disables its arrows until the request completes and shows pending/errors.
Other directions still use finite ONVIF steps; other cameras retain their existing hold behavior.
Stale clients sending START to an opted-in camera are rejected, not converted into repeating
native motion. `step-ptz.js` is separate from live-player code. PTZ stays outside the control popup.

This is not continuous hold/heartbeat integration and does not claim lower click latency:
the initial version established a fresh session on each gesture (short reuse is now described above).
All-direction homologation and continuous native browser leases remain later work. No extra movement is needed merely
to activate the reviewed profile; deployed HTTP catalogue checks are read-only.

Rollout completed September 14: build `b-33e3e8dbc83b`, exact camera-3 identity freshly verified
before internal activation. Authenticated HTTP catalogue reports `ptz_interaction=step` for
camera 3 and unchanged `hold` for both other units; the new module returns HTTP 200. No movement
was replayed to test deployment. Full Python suite plus separate lightweight Node DOM tests,
ruff and mypy passed; an additional API regression verifies busy rejection and STOP bypass.
Build capped at 512 MiB/one CPU; only app recreated, go2rtc retained its existing process.

## Native RELEASE delivery check — September 13 local / September 14 UTC

At 2026-09-14T01:57:54Z (September 13, 22:57 Brasília), the experimental camera-3 path
successfully prepared its exact-unit route, sent one RELEASE and obtained strictly correlated
delivery confirmation. No START, motion, light or sound was sent. Socket ownership was held
through preparation/release/cleanup and the socket was closed. This proves delivery under the
existing receipt contract, **not** physical STOP or a motion/latency benchmark. No firmware-wide
capability or production transport preference was enabled. The check cannot establish behavior
under packet loss, process death, or all SDK queue-rewriting variants.

The PTZ adapter now sends the full encrypted BA peer receipt for a correlated B9 application
reply (including explicit error replies), in addition to the node ACK. Receipt sequences are
allocated after START/RELEASE and advanced before send; they cannot collide with either motion
packet. Unknown/unrelated responses are still ignored, explicit error still overrides delivery
success, and RELEASE retransmissions retain their original packet identity.

Regression tests cover positive/error reply receipts, reversed routing, distinct sequences and
invalid envelopes. Ignored reproduction: `re/verify_camera3_native_ptz_release.py`; sanitized
result: `temp/camera3-native-ptz-release-20260913.json`. Host-only code, no container restart.
The subsequent short gesture was physically confirmed and finite-step integration is described
above. Continuous browser leases remain pending; do not repeat native START via the ONVIF loop.

## Exact-unit preparation increment — 2026-09-12

`p2p/ptz_prepare.py` prepares the experimental route using the existing brokered control session,
without allocating a direct-media link or sending START/RELEASE. It checks the enrolled opaque
camera association and reviewed device identity before opening the socket, then performs three
single-attempt correlated reads: product identity, version identity and current PTZ-axis evidence.
An exact product/model/revision/firmware/SDK/hardware mismatch stops before the axis read.
Only the requested, advertised axis can yield a prepared route. The total preparation budget is
finite and capped at 20 seconds; failure closes the socket, success transfers ownership to the
caller. Packet sequence allocation also handles 32-bit wraparound.

18 new fake-session tests cover ownership transfer, invalid budgets, cross-camera association,
identity mismatch, wrong/offline targets, failed/boolean error codes, unavailable axes, exhausted
handshake/final-read deadlines and constructor/handshake cleanup. Together with the existing
protocol/motion/route suite: 77 tests. Ruff and mypy (160 source files) passed. No live camera
commands, browser, build or container restart were used for this increment.

This is **not** a production capability grant: the future service must acquire `PtzOwners` before
preparation, retain ownership through cleanup and consume the route immediately, with cancellation
and operation-proof checks. Expected identity must come from reviewed backend evidence, never a
client payload. RELEASE-only delivery now passed as recorded above. Pending: controlled camera-3 gesture/STOP proof,
then integration with the driver and browser lease semantics. No native fallback policy activated.

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
Exact-unit route preparation and live RELEASE-only delivery now passed as recorded above. Next: a short
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
Bounded owner, route and preparation are implemented/tested offline (84 focused tests).
Live RELEASE-only delivery passed; next homologate a short camera-3-only gesture and STOP recovery before making it preferred.
No container restart or production driver switch in this increment.
