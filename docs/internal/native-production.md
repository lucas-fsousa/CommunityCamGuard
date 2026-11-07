# Native media delivery, not another finite trial

Current deployment is now native LAN UDP on all three installed units, with
driver-reviewed identities and a passed simultaneous300s group run after fixing
measured receive-buffer loss. See [field rollout](native-field-rollout.md) for
current results and limits. The TCP-relay investigation below is historical.

## Actual regression — 2026-11-06

Recovery fix4ac0dad is deployed: image6fbcb0cf0171/tag `wsl-native-lease-recovery`,
buildb-516badb91149, camera3 native/active165. CI6147tests/1skip and Image passed.
The artificial3/32-camera limits and NATIVE_SOURCE_CAPACITY field are gone.

Live600s observation completed with1188binary fragments/450618308bytes. It was
NOT uninterrupted: one media outage at217.2s caused six failed connection/retry
attempts before the seventh WebSocket delivered media. Max binary interarrival
gap12.696s. API stayed on the same active165 generation and recovered without app
restart/manual unblocking. An earlier15:57:24Z failure also recovered; both source
events identify kcp_assembly_timeout, confirmed TCP physical close/broker release,
no inner CLOSE receipt and joined consumer. This validates the permanent-block
fix, not the absence of streaming stalls. App218.3MiB/1GiB, OOM=false/restarts=0.
A post-recovery closed original recording was300.926667s HEVC1920×1080/AAC16k mono,
with the next16:06:41Z segment using the same epoch. No sound/light/motor action.

### Actual media-path mismatch, not an assumed LAN transport

The running native socket connects to a public TCP relay; `select_relay` accepts
only global IPv4 TCP entries. The legacy RTSP sockets connect to LAN cameras.
The saved July PCAP, summarized without payloads or decryption, contains a
dominant camera→app LAN UDP flow:2358538payload bytes/3854packets, with120716bytes/
2269packets in reverse. This is historical evidence for that captured unit/session,
not proof that the same LAN dialect can be applied unchanged to camera3/firmware.
The SDK notes already distinguish LAN/NAT channels and route optimization from
the implemented single-relay TCP path. Current kernel route selection returns
the same LAN interface for camera and relay, so a different chosen local interface
was NOT demonstrated as the cause.

Prioritize the actual media path and SDK route-selection implementation now,
not new controls or generic architecture. Do not claim the relay is the sole cause
of KCP gaps without packet evidence. The shared worker also discarded the numeric
timeout facts already captured by the singleton path; these are now propagated
through the per-camera summary/diagnostics with the EXISTING bounded whitelist,
so gap/window counters survive retry. No packet payload/credential history is added.

The subsequently installed group-plan image a73aefe/build b-88168866f405 left
camera3 blocked163 at15:42:03Z. This supersedes the earlier successful short
windows: they did NOT prove recovery in the shared-lease production path.
The event confirmed physical TCP closure, outer broker release, joined consumer
and no decoder cleanup failure; the inner CLOSE receipt was absent. The lease
was marked detached but the account was simultaneously marked cleanup-uncertain.
The leased adapter also lost the channel's fixed protocol code, so the existing
transient-retry classification could not run. We cannot infer the original TCP
failure's exact cause from that discarded information.

The correction carries the existing singleton retirement/retry contract into the
shared path, not a new wire sequence or arbitrary timeout. A synthetic five-camera
ordinary startup forces TCP EOF/no inner receipt, recovers on the SAME account,
then closes the entire group successfully. Missing physical close/outer release,
unknown opening failures or unsafe motor/siren cleanup still fence reuse.
Artificial camera-count caps were removed as requested; workers follow admitted
registered cameras and account/queue resources follow the explicit cohort. No
NATIVE_SOURCE_CAPACITY configuration is required. Live deployment results follow
after acceptance; other two cameras remain RTSP and no physical action is required.

Previous deployed runtime (2026-11-06): `70beb7b`, build `b-3fad324407cc`, camera3
native/active159. Generic startup now restores reviewed checkpoints up to runtime
capacity, reserving camera/domain/slot before spending each checkpoint. Three
synthetic sources pass ordinary startup/shutdown; public capacity remains ONE
and the Yoosee group plan is still pending. This is not a multi-camera rollout.
See [runtime restart](native-runtime-lifecycle.md).

CI6139tests/1skip, frontend contracts and Image passed. Image `d110105f882d` /
`wsl-bounded-native-restart` replaced only the app; hub/tunnel were unchanged.
Camera3 completed60s authenticated MSE:122binary fragments/46199177bytes, max
interarrival2.63s (not absolute latency), no reconnect. Read-only lamp GET200/verified;
both legacy sources stayed active with one base consumer each. App205.2MiB/1GiB,
OOM=false/restarts=0, three in-app FFmpeg processes. The last event at15:01:06Z
confirmed a media-lease detach (TCP socket/receipt and broker release confirmed,
consumer joined, cleanup_failed=false; shared socket closure intentionally false).
No later retry appeared in this observation. No sound/light/motor command was sent.

Previous deployed runtime (2026-11-06): `5ceece1`, build `b-ba01cbf86a08`, camera3
native/active156. Reviewed offers now lazily bind to the driver's server-owned
account, with exact-device PTZ grants and ready-lease gating. Source intents span
decoder recovery/backoff, but the last source must stop/join the account before
retirement can succeed. STOP confirmation precedes media detach. Public admission
remains one, with the existing checkpoint domain; the other two cameras stay RTSP.
See [account lifetime](native-account-lifetime.md) for the ownership contract.

CI6133tests/1skip, frontend contracts and Image passed. The app-only replacement
uses image `d1835e054877` (`wsl-shared-native-offer`); hub/tunnel were unchanged.
Post-deploy MSE completed60.0s:62binary fragments/51510162bytes, max interarrival
1.537s, no reconnect. Read-only lamp GET returned409 initially, then200/verified.
No sound/light/motor command or native experiment on the other cameras was sent.
The new14:36:45Z original archive epoch closed a299.977667s MP4 containing
HEVC1920×1080/AAC16000Hz mono; the next14:41:44Z segment used the same epoch.
No new retry event appeared in this short observation.
These results do not prove simultaneous-native or multi-day endurance acceptance.
App217MiB/1GiB, OOM=false/restarts=0, three in-app FFmpeg processes; the two legacy
sources remained active with one base consumer each.
Next delivery item: bounded public multi-source admission, not another PTZ mapping.

Previous deployed runtime (2026-11-06): `c82a053`, build `b-a5d5dd07a803`,
camera3 native/active153. CI6119tests/1skip and Image passed. Optional lazy driver
runtime services are now registered under the server owner, before native source
runtimes. The shared account owner, same-owner group publication and camera-local
recovery pass synthetic three-consumer integration, including a decoder failure
and explicit replacement using only one login/socket/SDK-owner scope. The normal
generic registry cleanup contract already supports lease detach; no vendor-specific
socket-retirement exception was added. See [account lifetime](native-account-lifetime.md).

Production offers remain the accepted single-source path. Reviewed shared offers,
per-lease PTZ authority and bounded public multi-source admission are still pending.
No other camera was migrated or tested natively. A post-deploy60s MSE connection
completed without reconnect:62messages/31604455bytes, max inter-message gap1.877s
(not live latency). Lamp GET-only200/verified267ms followed an initial409 response.
Legacy packet counters advanced; app194.4MiB/1GiB, OOM=false/restarts=0. A closed
299.885667s original archive remainsHEVC1920×1080/AAC16000Hz mono, with the next
segment under the same epoch. No sound/light/motor action was sent.

Previous deployed runtime (2026-11-06): `32632de`, build `b-210e76d55cfa`,
camera3 native/active150. CI6085tests/1skip and Image passed. Dynamic source leases
and a borrowed native-consumer/control binding are implemented and tested offline.
The default production path now uses the extracted exact-device control delegates;
one native source remains the admission limit. Driver account lifetime, group
publication, lease-specific prepared-source retirement and per-lease PTZ authority
still require integration. See [source leases](native-source-leases.md).

Post-deploy60s MSE completed without reconnect:62messages/17864715bytes, max
inter-message gap1.187s (not absolute latency). Read-only lamp GET200/verified148ms
and advancing legacy ingress counters passed; app181.3MiB/1GiB, OOM=false/restarts=0.
No sound/light/motor action or native experiment on the other cameras was used.

Previous deployed runtime (2026-11-06): `39a0f29`, build `b-d4580bc96801`,
camera3 native/active147. CI6044tests/1skip and Image passed. Broker ownership,
independent TCP/delivery channels, bounded group scheduling and isolated channel
retirement are implemented and tested offline for a three-channel bootstrap.
Production source admission remains ONE native camera; shared source leases and
dynamic attachment/replacement are not yet integrated. No extra SDK login or
native experiment was run on the other cameras. See [multi-camera scope](native-multi-camera.md).

Post-deploy camera3 acceptance:60.0seconds,63binary MSE messages/20310557bytes,
no reconnect, max inter-message gap1.62s (not end-to-end latency). The same source
remained active147; periodic meter ACK was observed and no new retry event was
logged. Legacy ingress counters advanced: yard5690→9178, garage3838→6232, each
with one base consumer. App181.1MiB/1GiB, OOM=false/restarts=0. This is a short
deployment regression, not simultaneous-native or long-endurance acceptance.

Previous deployed runtime (2026-11-05): `3b0b311`, build `b-58c43b444dfd`,
camera3 native/active144. CI6012tests/1skip and Image passed. The other two
cameras retain their existing RTSP sources; no camera action was used in this
investigation. A 900-second continuous MSE acceptance window completed without
reconnection or source/SDK recovery:1797messages/684417887bytes. Max inter-message
gap3.02s measures delivery gaps, not absolute live latency. Longer endurance and
simultaneous native cameras remain unverified.

Implemented corrections: fair video/AAC input scheduling, matching KCP receive
credit/window reports, reserved missing-head capacity, and safe retry of retired
pre-ACCEPT capacity/deadline failures. Actual KCP repairs at2.7–3.5s proved the
finite2s assembler cutoff was premature; continuous repair now uses the existing
5s media idle budget, without enlarging media buffers. The200ms TCP work budget
now yields with bounded pending bytes/ACKs rather than closing a valid socket
when host scheduling or decryption uses that time. Ambiguous writes still close
without replay, and absolute protocol deadlines still apply.

Ongoing MTP measurements now use that same TCP connection; the first fresh
periodic receipt was confirmed live. A read-only lamp query returned200/verified
in175ms during continued MSE. A closed300.964s original recording was identified
as1920×1080HEVC plus16kHz mono AAC, and the next segment used the same encoder
epoch. These are useful acceptance checkpoints, not a multi-day stability claim.

See [investigation and exact live outcomes](native-transport-investigation.md),
[TCP cooperation](native-tcp-cooperation.md), [KCP credit](native-kcp-window.md)
and [A/V fairness](native-av-fairness.md), plus [route maintenance](native-tcp-maintenance.md).
Short playback and unit tests are not
multi-day endurance acceptance; the earlier records below are historical.

Shared-native siren pulse is implemented with owner-driven timing, reserved OFF
and exact independent OFF-state confirmation. Camera3's authorized two-second pulse
was confirmed by its owner and the exact OFF readback. See [native siren](native-shared-siren.md).

Latest deployed runtime `22fc8d1` / `b-c66ccfb36649`, native/active105.
Native alarm catalogue/selection adapters are integrated. The public options
request returned200 with the two specifically reviewed resources during continued
MSE; all3online/recording, diagnostic disabled and no playback/selection actuation.
The expired exact-resource profile was deliberately re-reviewed against the fresh
native catalogue, not auto-renewed. See [shared alarm resources](native-shared-alarm-resources.md)
for that acceptance, the separate invalid-length transport failure/fix, and the
manual restoration of its already-blocked pre-fix generation.

The shared floodlight adapter is implemented with strict type12 reads/type11
boolean writes on the same account, plus mandatory readback. Subsequent authorized
camera3 ON/OFF testing was physically confirmed by the owner. See [shared floodlight](native-shared-floodlight.md).
Deployed `a59907e` / `b-64827371360c`, native/active96. Real lamp status GET passed
while the same MSE socket continued; all3online/recording, CI/Image passed. The
new write path is now physically accepted on camera3. Earlier runtimes below are historical.

Alarm-resource enumeration/selection now share that account too, without playing
audio. See [shared alarm resources](native-shared-alarm-resources.md) for current
test and deployment evidence; this does not homologate new sounds or devices.

Smart-protection switch/weekly-plan shared writers are implemented without a
second native account. Coverage is simulated; no guard was armed or schedule
changed for this integration. See [shared guard controls](native-shared-guard.md).
Deployed `c29aa8c` / `b-4737e1a2c4e1`, native/active93: real GET-only guard reads
passed with continued MSE on the same socket; all three cameras online/recording.
CI/Image passed. Earlier runtime IDs below are historical checkpoints.

The repeated2026-11-04 unavailable report exposed a separate startup gap, not
another measured encoder failure. See [abrupt Docker startup](native-abrupt-startup.md)
for namespace fencing, delayed readiness, supported layouts and actual acceptance.
The earlier encoder-recovery evidence below does not cover container death.

Current verified runtime: `8fbdf5a`, dashboard `b-20782a2db487`, native/active87.
The entire app was killed with SIGKILL (exit137, zero clean checkpoints). Ordinary
Docker start restored camera3's real MSE in6.1s, without the operator installer or
a DB patch, with all three cameras online/recording. Default Linux PID1 namespace
fencing and pending readiness are deployed; other host layouts still require
clean restart proof. No actual WSL shutdown or physical camera action was tested.

Latest runtime (2026-10-30): `575ed76`, dashboard `b-3422b2883cd9`, camera3 native
active revision62. All three cameras online/recording, others legacy0; no OOM or
unexpected app restarts. Capability renewal on the native owner was observed
read-only in production. Shared night-mode integration passed offline tests;
no physical night-mode command was sent. Full CI/Image passed. Earlier versions
and revision numbers below are historical deployment evidence.

The delivery target is camera3 on the ordinary Docker app and existing dashboard,
with persistent source selection, original HEVC/AAC recording and shared native
PTZ. Other cameras remain on their existing transports. No sound/light/motion
commands are required for installation.

The host operator command `scripts.enable_yoosee_native` stops the old ingress,
backs up the actual database/configuration privately, registers the target's
driver-reviewed media/combined PTZ catalogue without dropping its existing controls,
and uses normal native readiness/clean shutdown to create the startup checkpoint.
The app container then resumes it normally. It does not keep a trial server or
emulator running, nor copy fabricated checkpoints from a test database. Its
systemd cleanup hook restores the backup only if installation has not committed.
Recording files are never deleted by installation/rollback.

Native protocol support uses recorded exact model/firmware identity and explicit
driver review, not the30-minute age of a control-state observation. Expiring that
cache is not expiring authentication or changing the camera's supported protocol.
Clock expiry alone therefore no longer kills native video/PTZ. Explicit probe
invalidation, different identity/enrollment, revoked catalogue, authentication,
packet validation, scoped motion/STOP and single-capture exclusion remain enforced.
Other control-state freshness policy is unchanged.

Demand-driven control-capability refresh can now query the same native account,
without a second login or clearing the running source's identity mid-collection.
Publication is atomic and superseded/invalidated batches cannot overwrite newer
evidence. This does not extend failed/expired grants or add shared writes. See
[native capability refresh](native-capability-refresh.md).

The native source reports recording activity through the generic source registry,
so dashboard online/recording indicators do not depend on a legacy go2rtc producer.
Docker gives the app45s for a clean stop. Clean restart is supported; abrupt-kill
recovery and prolonged soak/extra hardening are deferred, not release gates.

Streaming, reviewed shared PTZ/state reads, verified speaker-volume and orientation changes
are provided by this native owner. Features that still require a separate account connection remain subject
to the existing shared-account exclusion; this delivery does not pretend all
native shared-write features have been implemented. Other-camera foreground
controls can cause a clean native-session yield/reconnect through existing logic.

The existing three-state night-mode control also has a typed shared-owner adapter
with one-send/D3/readback semantics. This integration was tested with synthetic
responses only; no physical night/IR mode change was made for that implementation.
See [shared night mode](native-shared-night-vision.md).

## Deployment follow-up (2026-10-29)

The first ordinary-container installation reached readiness but then blocked;
the follow-up installation rolled back because the container did not stay ready.
Do not confuse an installation marker or a passing finite trial with current
production availability. The other two cameras retain their legacy transports.

The native failure log now records only local closure facts and fixed protocol
classifications. TCP/AV error sanitization previously hid the underlying KCP
classification, so the narrow, already implemented timeout reconnection could
not run for failures raised during receive. The worker now walks a bounded
exception-context chain to recover that classification, without logging messages
or payloads. Reconnection still requires confirmed outer route release, both
sockets closed, confirmed motor stop when applicable and retired consumers.
Unknown protocol failures remain blocked. This correction passed 51 targeted
worker/recovery tests.

The corrected installation committed successfully in the ordinary app container
(`5de4e7f`, build `b-6932aecc11d2`). Camera3 is native/active at revision11;
the other two cameras remain legacy/active at revision0. An authenticated request
to the existing dashboard MSE WebSocket received H264/AAC initialization and three
binary fragments (1,165,374 bytes). All three cameras report online/recording and
all three recording destinations grew during the follow-up check. The installer
exited (169.5MiB peak, no swap); only the ordinary containers remain serving.
No sound, light or movement commands were sent. Existing shared native PTZ
implementation/physical acceptance is retained, not retested as a release gate.

### Continuous-use follow-up

Subsequent use reached blocked revision12: the decoder reported a frame-progress
`TimeoutError`, with consumer/decoder retired, both sockets closed, outer broker
release confirmed and no motor command started. The missing inner CLOSE receipt
incorrectly prevented any retry. The driver now accepts a stopped owner's confirmed
outer retirement and retries only the known decoder timeout (or the existing KCP
timeout classification). Format errors, unknown protocol failures, missing outer
release, unclosed sockets and unconfirmed motor STOP still do not retry. Recovery
is sequential under the existing reservation/backoff, not another capture or a
larger buffer. This corrects the production failure; the initial readiness check
above was not proof of continuous availability.

The correction (`655d907`, build `b-6272f03053d3`) is deployed; camera3 resumed
as native active revision17. A controlled stall targeted only its encoder PID60,
which was retired and replaced by PID91 under the same source selection, with
one encoder observed and no app restart. The recovered authenticated MSE player
received H264/AAC and three binary fragments (573,401 bytes). Other two cameras
kept recording during the test. No sound/light/motion/device configuration was
sent. 69 focused tests, Ruff and Mypy369 passed under bounded memory/no swap;
GitHub CI and Image both passed. This verifies the specific stall recovery,
not a claim of multi-day endurance.

Dashboard follow-up: a source-unavailable placeholder was not included in the
freeze watchdog (it has no player counters), and ordinary camera renders retained
it even when the native source recovered. The live view now polls source metadata
while such placeholders exist and replaces only the recovered camera's placeholder
automatically. Healthy neighboring players are not replaced and this never calls
camera/producer restart. EN/PT text no longer asks for a manual player refresh.
The Node contract verifies unavailable→available replacement and no healthy-player
flapping. The separate shared-volume implementation is not part of this UI fix.

Further production failure at revision21 occurred during a replacement route's
initial TCP measurement (`poll:117`, session deadline). No decoder/parser failure
occurred; both sockets and the outer route were retired, but the old classification
required prior media readiness and therefore poisoned all later retries. Fixed
local classifications now distinguish measurement/handshake/control-receipt/idle/
I/O timeouts and EOF from malformed protocol data. These known transport failures
may retry after confirmed outer/socket/motor retirement even before readiness.
Malformed/unknown data, authentication rejection and uncertain cleanup remain
blocked. No handshake deadlines, traffic limits or memory buffers are increased.

The deployment now includes shared speaker-volume writes, accepted on camera3
through the ordinary authenticated API with the same MSE socket continuously
receiving video (50→75→50, left50). See native-shared-volume.md for the delayed
readback correction and precise evidence. Current runtime is `2a211c9`, build
`b-be9a4c0476af`, source active revision32. Initial-handshake transport classification
passed107 focused tests and Mypy374. Other shared-write features remain pending;
this does not grant or claim unimplemented actions.

Orientation is now also deployed (`9784dd2`, `b-935096facb0c`), with camera3
normal→inverted→normal accepted by D3/exact readback while the same MSE socket
continued receiving video. Source stayed active revision35, volume stayed50 and
all three cameras reported online/recording. See native-shared-orientation.md.
No other camera was actuated. Human visual confirmation was not requested as
another release gate; this evidence is explicitly protocol/readback acceptance.
