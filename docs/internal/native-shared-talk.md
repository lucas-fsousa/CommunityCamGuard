# Shared-session outgoing voice — 2026-11-14

## Delivery boundary

The owner approved continuing toward shared-P2P outgoing voice. Production still
uses the physically accepted Yoosee RTSP/PCM backchannel. Incoming audio/video on
all installed cameras already uses native LAN UDP; this work does not change
those sources, their capabilities, credentials, settings or original recordings.
No physical command was issued for the incremental implementation; earlier finite
speech trials and their actual results are documented below.

Implemented: finite and incremental encoded-audio transmission inside the existing AV owner,
accessible through `AccountMediaChannel.begin_talk` on its selected UDP or TCP
transport. Neither route is selected by the dashboard or acoustically homologated;
their implementation is not an automatically granted driver capability.
The raw channel API is account-thread-only. A bounded thread-safe bridge now
connects the driver view to that owner, but no dashboard route selects it yet.

## Recovered protocol, not a codec guess

The Java setup initially configures outgoing AMR-NB codec5/option0,
mono/16bit/8000Hz,160 samples and mode7 (32-byte frames, TOC0x3c). Later SDK
received-header callbacks can reconfigure the intercom format; initial Java AMR
alone does not establish final TX. The exact-descriptor AAC comparison and its
limits are recorded in `native-talk-v1-channel.md`. The v1 serializer sends:

1. Audio-only `HEADER_ONLY`, marker0x0102, on the base KCP conversation.
   SDK DataType2 dispatches to `packing_header_only`; the old0x0500 descriptor
   serialization used the unrelated DataType6 packer. See the dispatch-table
   correction in `native-talk-v1-channel.md`; earlier tests preserved that error.
2. Encrypted sequenced builtin0x32 with payload1 on the base AV conversation.
3. V1 audio records with epoch-microsecond PTS and four20ms frames per burst
   (native threshold64000us), followed by any one-to-three-frame tail.
4. Builtin0x32 with payload0 on that same base AV conversation.

The channel selection above is corrected by the narrow SDK reinspection on
2026-11-15; the earlier high-channel assumption belonged to the V2 special case,
not this V1 receiver. See [binary evidence](native-talk-v1-channel.md).

The existing protocol packers are reused. Old standalone `ModernIntercomSession`
is **not** attached to the live socket: it owns blocking reads and AV START/CLOSE,
which would compete with the video owner. Previous standalone transport receipts
did not establish intelligible speaker playback; this candidate is not labelled
homologated on the strength of ACKs.

## Integrated ownership

`CooperativeTalk` has no socket, login, receive loop, sleep or AV lifecycle commands.
`AvHandshake` remains the only owner of parsing, incoming media and outgoing
sequences. START retains base sequence0; the encoding header, audio bursts and
eventual AV CLOSE allocate subsequent base sequences without resetting them.
Microphone ON/OFF now allocate base sequences after the encoding header/audio,
not high-channel INIT sequences. Repeated messages never reset that counter.

If a superseded UDP INIT lacked its receipt, video reception may legitimately be
ready under the existing policy. Its exact emitted wire is now retained for late
ACKs without a video deadline. **V1 speech must not resume or wait on those
high-channel requests:** it uses only the independent base AV conversation, whose
START receipt is already a readiness prerequisite. The former drain gate was
left over from the incorrect V2-style high-channel microphone implementation.
Resuming it could fail a live V1 route before sending the first speech frame.
It was removed on2026-11-23. Retained INITs still accept real late receipts;
neither acceptance nor base START/header/audio/OFF receipts are fabricated.

The existing reader feeds ACKs to the outstanding talk request AND feeds the same
datagram to the AV receiver. UDP uses its existing validated route; TCP keeps its
existing relay wrapper/output pump. There is no additional camera connection.

## Bounds and termination

Input is an immutable tuple of1–500 validated AMR frames (maximum16KiB/ten seconds).
Header/microphone controls remain sequential. Audio now has a bounded window of
at most eight outstanding PUSHes (32frames/640ms of content), rather than waiting
for each batch's ACK before the next80ms batch. Each PUSH retains at most four
identical transmissions at250ms intervals and a two-second receipt deadline. The entire utterance is
bounded to16seconds including negotiation, playback cadence and termination.
Pacing follows actual emission; scheduler stalls cannot produce catch-up bursts.
The final burst gets its playback duration before normal microphone OFF.

Cancellation discards unsent audio but cannot skip an already emitted KCP sequence.
After its receipt, microphone OFF is sent if microphone ON was emitted. Cancelling
before any emission sends nothing. A requested AV shutdown first drains this
microphone termination and only then allocates AV CLOSE. Ordinary message finish
does **not** close AV or the video delivery.

A missing PUSH receipt invalidates the route rather than pretending a skipped
sequence or unconfirmed microphone OFF is safe. Local close clears retained
audio/cookie references but does not fabricate remote stop evidence. Receipt flags
on the returned handle mean transport receipt only, never sound reproduction.

## What remains before selecting this in production

- One announced, authorized known-speech test on camera3, with speaker confirmation
  and video/recording observation. No family-wide `p2p_amr` grant from video success.
- Separate physical hold-to-speak acceptance before enabling its stream proof.

Deterministic regressions exercise actual UDP/TCP framing, shared-reader video,
INIT and repeated-message sequence ownership, rejected input, cancellation during
header/start/audio, CLOSE ordering, receipt loss, final-tail cadence and a complete
ten-second message. These are software contracts, not an invented physical test.
Production image remains unchanged while this candidate is not selected.

Local result:144 focused tests passed under160MiB/no swap/25%CPU,62.6MiB peak;
Ruff and application-wide mypy passed (192MiB cap,97.9MiB peak). Full-suite CI
runs remotely rather than loading the WSL host. Existing AV control deadline
messages remain unchanged for the production failure classifier.

## Driver/account bridge, same-session continuation

`SharedDeviceControls` now delegates submit/take/cancel to its current
`AccountReadLifecycle`. Submission accepts encoded frames only, not an arbitrary
device ID; `MediaLeaseInbox` resolves the driver's own camera ID to a claimed,
uncancelled, live lease. Tickets carry the account generation and the request
retains the exact lease serial. Replacement leases cannot inherit queued audio.

`TalkInbox` uses a lock and at most one utterance per lease; unread outcomes also
retain their slot. Total capacity follows the account's camera cohort, without a
fixed camera-count ceiling. Cancelled queued audio is cleared immediately. Active
cancellation is consumed cooperatively by the account owner, not another socket
reader or a blocking request thread. The existing `MediaLeasePump` polls this
handoff on normal worker turns; no new worker/authentication/media route is opened.

Results say `transport_received`, `cancelled` or `unavailable`, with exact audio
frame/OFF receipt fields. Account shutdown never fabricates an OFF confirmation.
The bridge itself does not grant a capability, select the unhomologated transport,
or issue commands without a future explicitly admitted driver operation.

Bridge verification:88 focused tests passed under160MiB/no swap/25%CPU
(70.1MiB peak), including an actual synthetic `AccountReadWorker` run proving
the utterance uses its existing transport. Stale-generation cancellation, exact
lease binding, queued/active cancellation and shutdown receipts are covered.
Ruff and application mypy passed (93.6MiB peak for the latter). Tests initially
used an incorrect summary field/reason; those assertions were corrected to the
worker's existing `reason="stopped"`, without changing worker termination logic.

## Physical review preparation

Owner authorized a physical camera3 speaker test, followed by dashboard selection
only after confirmation. Added the canonical16kPCM-to-AMR adapter and an opt-in
single-use `/api/internal/diagnostics/native-talk` route: primary session, direct
loopback only (no forwarded/proxy headers), fixed server-configured camera ID,
bounded PCM body, normal camera operation lock. Defaults are disabled; no public
target/transport/credential parameters or capability grants. It submits through
the live shared account, never an independent login or RTSP fallback, and returns
transport receipts with `speaker_verified=false`. No physical result yet.

First authorized live attempt: diagnostic HTTP200/unavailable in0.134seconds,
zero audio-frame/OFF receipts. Account log explicitly identified unconfirmed
outgoing INIT sequences; no audio was emitted. All three stayed online/recording.
The prior implementation discarded superseded INIT senders at ACCEPT, preventing
late receipts from being counted. Retaining them and draining identical emitted
packets on explicit talk demand corrects that concrete prerequisite; acoustic
acceptance remains pending. No volume change (readback50%, verified).

Second live attempt after INIT correction: HTTP200/cancelled in16.581seconds,
132/500 audio frames acknowledged, no OFF receipt. Owner explicitly confirmed
**no sound**. This is a failed speaker test. Camera3 then became blocked while the
other two stayed active. Returning the app to the previously accepted stable
image restored all three online/recording; no camera reboot/settings operation.

The transmitter also differed from the SDK ring-buffer callback by waiting for
each audio-batch ACK before producing another. That can stretch the20ms codec
cadence under delayed receipts. Audio now uses an eight-PUSH bounded window with
independent80ms pacing, while still draining all outstanding sequences before
OFF. A synthetic350ms-ACK-delay test completes all500frames under13seconds with
video still active. This corrects that pacing discrepancy; it does NOT establish
that it caused the physical silence, nor an acoustic pass. New physical review
remains required before dashboard selection.

Third authorized attempt on `5ae4a97` / image `119a3995f452`: HTTP200,
`transport_received`,500/500 AMR frames and microphone OFF transport receipt,
10.409seconds. A simultaneous authenticated camera3 HD observer received13 binary
WebSocket messages/4292860bytes, maximum measured gap1.078seconds, trailing gap
0.5seconds, no observer error. Final sources were native/active324/54/51 and a
subsequent status check confirmed all three online/recording. No volume change,
siren/light/movement command, second camera connection or dashboard route grant.

**Speaker confirmation is still pending for this third attempt.** The owner's
earlier "Não saiu som" applies to the second, interrupted attempt, not acceptance
or rejection of this later completed one. CI and Image for `5ae4a97` passed. The
inventory classification omission in the first diagnostic commit was separately
fixed by `1100fa5`; its CI passed too. Current container runs the review image,
not a promoted `latest` tag. The local diagnostic has consumed its one attempt;
normal dashboard voice operations still select their existing RTSP/PCM route.

## Physical validation deferred; software-only continuation

Owner subsequently confirmed they are no longer near the camera. The third
attempt remains acoustically unvalidated; no further physical test is permitted
in this continuation and shared-P2P voice is not selected in the dashboard.

The failed second attempt exposed an independent recovery integration omission:
`native talk deadline exceeded` and generic KCP send receipt timeouts were not in
the fixed failure-code table. They now project `talk_session_timeout` and
`kcp_send_timeout` and are eligible for the **existing** media-lease recovery only
after confirmed detach, stopped consumer/decoder and no cleanup failure. Unknown
protocol failures and uncertain closure remain fenced. This permits video-only
recovery; no utterance is queued/repeated and no capabilities/checkpoints are
fabricated. Live fault injection is not part of this software-only verification.

`TalkLeasePump` also distinguishes route disappearance from caller cancellation:
the former returns `unavailable`, retaining actual frame/OFF receipts, not the
misleading `cancelled` observed in attempt2. Explicit caller cancellation keeps
its existing result and cooperative microphone-OFF drain.

The ignored operator compose override now disables the physical-review endpoint
for subsequent deployments. Local recovery/worker/bridge regressions passed
210tests under160MiB/no swap/25%CPU (86.5MiB peak); no camera command was used.

Caller-lifetime follow-up: the PCM adapter previously called ordinary cancellation
when its18second wait expired. Queued cancellation retains an unread result by
design, but that departed caller would never collect it, leaving the exact-camera
slot permanently busy. It now abandons the ticket: queued work is discarded before
claim; claimed work stays reserved/cancelled until the owner drains cleanup, then
the result/slot is removed. Explicit cancellation still retains its inspectable
result. Stale generation/serial abandonment cannot affect replacement work. No
extra session, retry or microphone command is introduced by this API.

36focused bridge/lifecycle/adapter regressions passed under160MiB/no swap/25%CPU
(72.4MiB peak), including a simulated expired PCM request that can submit again
after cleanup. Runtime rollout/disabled-diagnostic checks follow separately.

Software-only rollout completed: commits `66155b2` and `2aae3fb`, both CI/Image
green; image `2e6889c0b4ef`, promoted to the normal `latest` tag and started through
the base compose file (no physical-review environment override). The running
configuration explicitly reports diagnostic=false; a primary-authenticated empty
local diagnostic request returned404 before any audio parsing/submission.

Read-only post-deploy camera3 HD observation:30seconds,32 binary WebSocket
messages/11303163bytes, max measured gap1.061seconds, no observer error. Final
sources native/active327/57/54; all three online/recording before and after the
observation. App352.2MiB/1GiB,81.44% of one CPU core; hub6.25MiB/0%; OOM=false,
restart count0. This checks ordinary delivery, not live talk-fault recovery or
acoustic acceptance. No sound/light/PTZ command, camera configuration change,
new audio proof or dashboard transport selection was performed.

## Dashboard message dispatch prepared, NOT activated

The existing message service/HTTP path can now dispatch a finite PCM message to
the shared owner **only** when the exact current unit/firmware has an explicit
writable `audio_message` proof with route `shared_p2p_amr`. That is distinct from
legacy `p2p_amr` and `rtsp_pcm`; neither old proof nor native-video success grants
it. No production profile/enrollment was changed. Existing installed cameras
continue selecting RTSP/PCM. Physical acceptance must precede any operator grant.

The generic `AudioMessageResult` now distinguishes a retained shared session and
microphone STOP transport receipt from an exclusively opened/released route.
Shared success leaves `route_released=false` and `direct_connection=false` rather
than fabricating either. Its optional shared fields do not change legacy response
shape. Completion still needs every frame and STOP receipt; it is not acoustic
verification. Backend route failures do not replay the message via RTSP or another
P2P login. The existing UI consumes the same `completed` outcome; no new button,
automatic test or default route change is needed.

Message and stream remain independent operations: a message proof cannot enable
hold-to-speak or consume its microphone iterator. The incremental adapter below
now handles separately admitted streams; both physical checks remain pending.

62focused driver/capability/PCM/HTTP regressions passed under160MiB/no swap/25%CPU
(72.9MiB peak); Ruff/mypy passed. That dispatch-only checkpoint was source-only,
with recovery image `2e6889c0b4ef` then running; the incremental rollout below
supersedes that runtime.
No physical test, capability registration or route activation was performed.

## Incremental hold-to-speak software implementation

`StreamingTalkInput` is a single-use, exact-lease handoff: at most25 encoded frames
(500ms/800bytes) queued,500frames total. Only the existing HTTP producer may wait
for queue space (bounded to one second); the AV account reader only takes available
four-frame bursts without blocking. EOF flushes a one-to-three-frame tail. Binding
the same input to another lease or consuming it twice is rejected.

The existing `TalkAudioWindow` handles either immutable messages or incremental
input with the same eight-PUSH reliability/pacing bound. Cancellation discards
unsent input, wakes blocked producers and drains already emitted sequences before
microphone OFF; video stays open. Two seconds without producer progress, or an
eleven-second input wall bound (ten-second capture plus startup margin), cancels
only the utterance rather than raising a video receive error. Genuine missing
KCP receipts still require the existing safe route-retirement policy.

`shared_talk_stream` encodes canonical16kPCM incrementally through one8kAMR encoder,
submits to the current account and returns only after full frame/OFF receipts.
Codec initialization precedes microphone submission. Bad/empty/oversized PCM,
owner changes and producer failure abandon the exact ticket without replay/login
or early slot reuse. There is no new socket, account thread or audio-history file.

Driver `audio_stream` selection requires its own fresh exact-unit/firmware writable
`shared_p2p_amr` proof and a retained native owner. No production proof was created;
RTSP/PCM remains selected for all installed cameras. The existing browser hold
control/WebSocket can use this adapter after explicit physical homologation; no
vendor-specific frontend protocol or speculative feature grant is introduced.

119focused protocol/queue/driver/API regressions passed under160MiB/no swap/25%CPU
(76.3MiB peak); Ruff/mypy passed. The synthetic maximum-duration stream feeds one
frame every20ms, delays transport receipts350ms, confirms500frames/OFF under13s
and continues incoming video. These are software contracts, not microphone,
speaker or browser-device acceptance. No production proof was registered.

### Incremental software rollout (no physical test)

Commit `156c3e1`, CI/Image green, image `d57eeff18fec` built under384MiB/no swap/
25%CPU and promoted through normal base compose. An isolated96MiB/no-network
container also verified the actual native AMR encoder and three-frame EOF tail.
No emulator, GUI browser, camera write, synthetic sound or microphone was used.

Running configuration confirms the physical diagnostic disabled (HTTP404).
Read-only persisted-proof inspection found camera3's message/stream grants still
`rtsp_pcm`; field units retain legacy audio selection. No installed unit has a
`shared_p2p_amr` grant for either operation. The new adapter is installed but idle.

Read-only camera3 HD observation:30seconds,32 binary messages/10676603bytes,
max measured gap1.185seconds, no observer error. All sources native/active330/60/57;
all three online/recording before and after. App355.9MiB/1GiB,103.14% of one core;
hub5.91MiB/0%; OOM=false/restarts=0. This checks ordinary post-deploy delivery,
not new talk playback or camera-side failure recovery. Physical message/hold
acceptance, then explicit driver activation/dashboard testing, remain deferred.

CI follow-up: the documentation-only run `43669541763` exposed an intermittent
`CancelledError` at TestClient context exit in the existing native viewer drain/
resume test. The resumed connection received media, then its context cancelled
ASGI before handler cleanup finished. The test now explicitly disconnects and
awaits its existing ASGI-completion event for each sequential connection; it still
asserts reader retirement and that the shared source remains running. No exception
suppression, longer production timeout or server-code change. Both endpoint paths
passed ten consecutive isolated runs (20case executions),160MiB cap/71.2MiB peak.

Physical tests were later authorized again. A finite known-speech retest completed
500/500frames/OFF receipt in10.680s, with HD still active, but the owner confirmed
silence. This is a failed acoustic test, not pending/accepted playback. Narrow SDK
analysis then identified the V1/V2 microphone channel mismatch; see
[correction evidence](native-talk-v1-channel.md). No new route proof was granted.
