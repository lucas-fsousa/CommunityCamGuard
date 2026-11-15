# Shared-session outgoing voice — 2026-11-14

## Delivery boundary

The owner approved continuing toward shared-P2P outgoing voice. Production still
uses the physically accepted Yoosee RTSP/PCM backchannel. Incoming audio/video on
all installed cameras already uses native LAN UDP; this work does not change
those sources, their capabilities, credentials, settings or original recordings.
No sound/light/movement command was sent for this implementation.

Implemented: a finite encoded-message transmitter inside the existing AV owner,
accessible through `AccountMediaChannel.begin_talk` on its selected UDP or TCP
transport. It is **not yet a dashboard transport**, an acoustic acceptance, a
streaming hold-to-speak adapter, or an automatically granted driver capability.
The raw channel API is account-thread-only. A bounded thread-safe bridge now
connects the driver view to that owner, but no dashboard route selects it yet.

## Recovered protocol, not a codec guess

The previously inspected IoTVideo `LivePlayer` path configures outgoing AMR-NB
codec5/option0, mono/16bit/8000Hz,160 samples and mode7 (32-byte frames, TOC0x3c).
It does not copy the incoming AAC descriptor. The recovered v1 serializer sends:

1. Audio-only `HEADER_ENC`, marker0x0500, on the base KCP conversation.
2. Encrypted sequenced builtin0x32 with payload1 on the high-bit conversation.
3. V1 audio records with epoch-microsecond PTS and four20ms frames per burst
   (native threshold64000us), followed by any one-to-three-frame tail.
4. Builtin0x32 with payload0 on the high-bit conversation.

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
Microphone controls start after every allocated high-channel INIT sequence, not
at zero or a hard-coded one. Repeated messages keep both counters.

If a superseded UDP INIT lacked its receipt, video reception may legitimately be
ready under the existing policy. Its exact emitted wire is now retained for late
ACKs without a video deadline. A requested utterance resumes a bounded receipt
drain of those identical wires before allocating audio/microphone commands: no
new INIT sequence, application request, authentication or camera connection.
Cancellation before microphone use suspends that drain and leaves video active.
An unsuccessful active drain still fails rather than inventing receipt evidence.

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

- PCM capture/encoding and hold-to-speak adapters using that bridge, preserving
  the generic driver's per-unit capability decision and current working fallback.
- One announced, authorized known-speech test on camera3, with speaker confirmation
  and video/recording observation. No family-wide `p2p_amr` grant from video success.

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
