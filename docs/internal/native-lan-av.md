# LAN AV selection on the shared account reader

Current production policy: use native TCP unless the driver has an exact current
native_video proof containing BOTH hevc and lan_udp. Camera3 now has live LAN AV
evidence documented below; rollout remains exact-unit, not brand-wide. A
successful LAN meter alone is not AV support.

## Initial implementation (historical pre-acceptance checkpoint)

This follows the first live LAN roundtrip documented in
[LAN selection evidence](native-lan-selection.md). It reuses the existing
AvHandshake/AvReceiver/KCP/V1 engine, not a second protocol implementation.

- Leased sources explicitly admitted by the driver's LAN AV proof measure the
  authenticated broker-provided LAN candidate before constructing a media
  transport. The probe is cooperative and expires in1.5s. Other sources start
  native TCP directly, without these probes.
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

## Latest live outcome and safe production handoff

Commit4e56bdd passed CI42300238379 and Image42300237651 and ran as
image67fbc704f803. The ACK correction did NOT establish live UDP AV: INIT had
transport receipt, but no ACCEPT/START/media arrived before the existing
handshake deadline. TCP recovered. The saved manufacturer's trace independently
contains107 client-to-LAN meter ACKs with the corrected four-byte marker.

One explicit temporary image compared the captured HD startup profile against
our combined legacy/packed preconnection cache. Base4e56bdd changed ONLY startup
userdata[23:25] back to12/00, preserving the legacy HD byte2. Experimental image
6736b57697b1 used a temporary Compose override for30s, with an EXIT trap restoring
the normal image. It also failed to obtain ACCEPT and recovered on TCP. Image
67fbc704f803 was confirmed restored. The experimental profile was NOT merged,
installed as latest or persisted as a device setting; it does not establish a
different platform version or justify silently reducing production quality.

Production now declines unaccepted UDP transport: a private typed preference is
supplied only by the Yoosee driver's validated exact-identity native_video proof
with lan_udp support. Without that evidence, the existing TCP owner starts
directly; it does not send experimental LAN meters, NAT punches or UDP INITs on
every server restart. This is not a camera-count/brand restriction or a public
switch that invents hardware support. Explicit protocol tests retain the UDP
path and verified same-account TCP fallback.

Remaining protocol gap is precise: metered LAN reachability and INIT transport
receipt are proven, but application-level AV acceptance is not. Do not mark UDP
video, LAN-only operation or the original freeze permanently resolved. Keep
future trials camera3-only, using this exact checkpoint rather than redoing
discovery, broad firewall changes or quality guesses.

## Deployed default-path checkpoint — 2026-11-06

Commit7e7f9d2 passed CI42302705359 (6171passed/1skipped plus frontend
contracts) and Image42302705156. Local targeted batches60passed, Ruff and
Mypy424files passed, with test peaks97MiB and69MiB. Capped local image
a2e53439a829/build b-b42008a87cd3 replaced only the app after CI succeeded.
No field-camera transport migration or camera-control command was performed.

Camera3 entered native/active revision198 directly, without LAN-probe,
UDP-negotiation-failure or handshake-timeout logs. Its authenticated MSE
consumer received122binary fragments/39538901bytes over120seconds, without
reconnection or source revision change; maximum fragment gap1.367seconds.
These are delivery measurements, NOT decoded-frame validation or absolute
camera-to-browser latency. The current archive grew from0 to1572864bytes;
the preceding closed archive retained1920x1080HEVC/AAC16kHz mono. App OOM=false
and restart count0. This short observation does NOT supersede the earlier
long-window TCP interruption or establish live UDP AV acceptance.

## SDK application retry found after the TCP handoff

Scoped inspection of the pinned split (SHA510bc51a, never the older ELF's
offsets) exposed a concrete difference from our initial handshake:

- iv_start_process_transfer0x251740 invokes iv_on_timeout_send_reqconn once,
  then creates a50ms timer at0x25176c–780.
- iv_on_timeout_send_reqconn0x24f2cc tests session+0x204, then queues action1
  at0x24f318–324 while application acceptance is absent. It does not stop at
  the first KCP ACK. Normal branch starts with25requests and subsequent timer
  returns are70ms,100ms,350ms (special branch uses180ms/16requests).
- iv_send_av_stream_ctl_frm0x24f628–650 calls ivkcp_send for every non-START
  request on the control KCP channel. ivkcp_send0x2672f8–340 appends a new
  queued segment; this is not identical-sequence transport retransmission.
- The saved app PCAP independently contains one call with INIT KCP sequences
  0,1,2,3 at relative0/52/121/220ms, and another with0,1 at0/50ms.
  Only structural counts/timing were printed; no raw payload or identity leaked.

AvInitRequests now reproduces that normal bounded application retry profile
for cooperative UDP only, preserving the same call/userdata and using distinct
control KCP sequences. Each request still owns its original identical-byte KCP
retries/receipts. ACCEPT stops application repetition; a superseded INIT ACK
cannot kill accepted media later. The existing5s readiness deadline, START/CLOSE
sequences, TCP handshake and production driver's default-TCP gate are unchanged.
Offline tests cover repeat-after-ACK, stop-at-ACCEPT, delayed last receipt and
the unchanged readiness deadline. Live effect remains to be measured; this is
a wire/SDK-backed discrepancy, NOT yet a proven cause of absent ACCEPT.

The same split's ACCEPT handler sets session+0x204 to1 at0x251640–648,
confirming what stops those requests. At0x251614–620 it sends local START
immediately after ACCEPT, without waiting for peer START. UDP follows that
ordering too; final readiness still requires the peer's START and encoding.
The historical TCP ordering remains unchanged.

## Live UDP delivery accepted — 2026-11-07

Code1479f1f passed CI42304469586 and Image42304469733;71targeted tests,
Ruff and Mypy425files passed locally with caps. Default image7587f34c97a2
retains driver-selected transport; temporary imagef8b57d021705 changed ONLY
camera3's private transport preference and added numeric INIT-close diagnostics.
Neither temporary change was merged or tagged latest.

First40s trial reached `native UDP AV ready transport=lan`; the initially late
MSE observer was closed with1012 by the PLANNED restore, not a spontaneous stream
failure. The repeat trial waits for active source before observing, then restores
the default image only AFTER the observer exits. Results:

- Measured LAN roundtrip in one attempt, then UDP AV ready; no TCP fallback log.
- Native source revision207 remained active throughout60seconds of authenticated
  MSE reception:62binary fragments,19941723bytes, maximum fragment gap1.175s,
  no WebSocket closure or source revision change.
- The closed archive from this exact trial is62.958s/720263bytes,
  original1920x1080HEVC plus16kHz monoAAC. No quality reduction was introduced.
- Both trials restored the default image through EXIT cleanup; no emulator,
  extra account/login, firewall mutation or light/sound/motor action was used.

This closes the missing live LAN UDP AV acceptance/delivery checkpoint for
camera3 and justifies its exact driver's lan_udp proof. It does NOT establish
long-term freeze resolution, end-to-end browser latency, other units' acceptance
or LAN-only authentication. Native TCP remains the fallback and remains default
for units lacking this live AV proof. Normal-image rollout is recorded below.

## Normal-image driver rollout — 2026-11-07

Camera3's trusted native_video proof now includes lan_udp after verifying its
current enrollment, identity snapshot and HEVC read proof. All other operation
proofs, provenance references and rollout controls were preserved. The new
native_video reference uses the digest of this document at commita2e0024;
subsequent documentation edits do not change that reviewed source snapshot.
No raw credential, app proxy or public transport toggle was introduced.

The NORMAL image7587f34c97a2/build b-dc4b074403bc was restarted and selected LAN
UDP through that proof, not through the temporary override. Its log confirmed
roundtrip attempts1 and UDP AV ready. Source revision213 remained active over
another60s MSE observation:62fragments/20319865bytes, max gap1.233s, no closure
or revision change. The new archive grew from0 to786432bytes. Both field cameras
remained legacy RTSP. App178.6MiB/1GiB, OOM=false/restart count0; explicit operator
restarts/recreates during testing still interrupted consumers temporarily.

The active transport integration is delivered. Next investigation is sustained
operation and any actual new interruption evidence, not another INIT/ACCEPT
implementation prerequisite. Do not extrapolate this short result to overnight
stability or silently migrate untested field cameras.

## Overnight observation — 2026-11-07, 13:41–13:46 UTC

The normal image remained up since03:04:20Z; UDP-ready was logged once at
03:04:23.862Z. Camera3 still held source revision213, with no native failure or
retry records since rollout in the existing persistent event log (file present).
No service restart, new negotiation, image change or camera action was needed.

A disposable read-only/no-network archive audit was capped at128MiB/0.25CPU
and removed automatically. All127closed segments from this exact recording
epoch passed ffprobe metadata inspection:38100.161s total, individual durations
294.016–305.632s. Filenames advanced294–305s between segments; the next segment
was growing. This checks container metadata and segment continuity, not every
decoded frame or the physical accuracy of the camera's on-screen clock.

A new authenticated60s MSE observation after over10hours delivered62fragments/
51614066bytes, max inter-fragment gap1.598s, no closure or source revision change.
The read-only white_light API initially returned409 while the one-hour
capability snapshot refreshed on demand; the next read returned200/verified/
authenticated in231ms. No second account login or media session replacement
was observed. This was a state read, NOT a lamp activation/deactivation test.

App218.3MiB/1GiB, OOM=false/restart count0, WSL available memory over3GiB.
Both field cameras remained legacy RTSP. This is the first overnight UDP
checkpoint passed, not a guarantee of indefinite stability or proof of
end-to-end browser latency. The delivered handshake/transport gates are closed;
future failures require their own concrete evidence.
