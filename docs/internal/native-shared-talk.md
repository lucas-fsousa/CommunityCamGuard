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
The API is account-thread-only, not safe for direct calls from HTTP workers.

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
ready under the existing policy, but talk admission is refused without affecting
that video. This candidate does not assume those outgoing sequence gaps are safe
or force an account reconnect to obtain permission. Handling that ambiguity in
the future adapter remains explicit work, not a hidden retry or readiness change.

The existing reader feeds ACKs to the outstanding talk request AND feeds the same
datagram to the AV receiver. UDP uses its existing validated route; TCP keeps its
existing relay wrapper/output pump. There is no additional camera connection.

## Bounds and termination

Input is an immutable tuple of1–500 validated AMR frames (maximum16KiB/ten seconds).
One PUSH is outstanding at a time: at most four identical transmissions at250ms
intervals, with an absolute two-second receipt deadline. The entire utterance is
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

- A bounded, generation-bound submission/result/cancellation bridge from the
  driver to the existing account thread and exact active camera lease.
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
