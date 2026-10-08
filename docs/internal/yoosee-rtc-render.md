# Yoosee RTC: player/render boundary

Offline checkpoint, 2026-10-08. This continues the
[receive/decoder timing evidence](yoosee-rtc-timing.md), using the same pinned
`libgwplayer.so` (SHA-256
`95f1348832ec56271dd0f2466d0c8c2fab19dfc390d93a25384918a097220e5e`).
Addresses below are virtual addresses, not file offsets. No camera command or
proprietary-library execution was involved.

## Stale output gates

`APlayer::Impl::onReceiveFrame` (`0x1dce78`, 2596 bytes) compares the incoming
decoder pointer against both current decoder pointers (`this +0xe8/+0xf8`) at
`0x1dceac–0x1dcec0`. If neither matches, it logs and exits through `0x1dd4e0`.
An accepted decoder then faces an equality check between frame metadata +0x178
and player +0x29c at `0x1dcf1c–0x1dcf28`. A mismatch takes the
`ignore different playback sequence` branch and the same exit.

These are concrete decoder-identity and playback-sequence guards, not merely
log-string guesses. The compared playback sequence must not be confused with
the RTC wire packet sequence byte. This does not establish the lifetime or
increment policy of the player sequence counter.

Renderer initialization is conditional on further per-stream state. The path at
`0x1dd330` locks a recursive mutex, retains the frame pointer, copies `AVExtInfo`
at `0x1dd400`, and invokes `start_render_nolock` at `0x1dd40c`, unlocking at
`0x1dd4c4`. This is not proof that every callback restarts a renderer or presents
a frame immediately.

## Synchronization setup is separate from the wire parser

`start_render_nolock` (`0x1e6c18`, 3504 bytes) requires the demuxer pointer
at player +0xd8. The missing-pointer branch reaches the demuxer-not-open error.
Otherwise it invokes `setup_sync_strategy(false)` at `0x1e6c68` before its
audio/video-specific setup.

`setup_sync_strategy` (`0x1cb7bc`, 1996 bytes) reuses an existing strategy or
constructs one. With an existing strategy and a false argument it calls
`resetQueues` (`0x1cb7f4`); absent a strategy, configured type zero is replaced
by numeric type 1 before construction (`0x1cb7fc–0x1cb838`). **The semantic name
of type 1 is not established here.** Call sites register packet, decoded-frame
and in-decoding queues via `setAVPacketQueue` (`0x1cb978`), `setAVFrameQueue`
(`0x1cba28`, `0x1cbcf0`) and `setAVDecodingQueue` (`0x1cbbb8`, `0x1cbe9c`).

`resetQueues` (`0x1f17f4`, 72 bytes) locks its mutex and clears four maps of
shared queue references (+0x10/+0x70/+0xd0/+0x130). It does **not** call a
queue-content flush in this body. Releasing the last reference can destroy an
object; clearing these maps alone must not be described as flushing every
producer's queue or dropping all buffered frames.

## Clock interpolation helper

`AVSyncStrategy::Clock::computePts(bool)` (`0x1ef528`, 136 bytes) returns the
stored base at +0 when interpolation is disabled, the anchor at +0x10 is zero,
or the extent at +8 is nonpositive. Otherwise it calls `tick::steadyTime`,
converts that value with integer arithmetic, subtracts the anchor, clamps the
elapsed value to nonnegative, multiplies by the double at +0x28, converts to an
integer, caps it at the extent, and adds the stored base.

This proves bounded local clock interpolation, not UTC conversion or a fixed
network-buffer duration. The [synchronization follow-up](yoosee-rtc-sync.md)
establishes monotonic microseconds and audio update semantics; the concrete
audio-output producer remains to trace. It does not prove the clock is always
the audio master or that the browser's catch-up behavior uses this algorithm.

## Presentation commit helper: caller still unproven

`commitPresentedFrame` (`0x1f0ef0`, 308 bytes) was inspected separately. Under
the strategy mutex it requires decision +0 to be zero, decision +0x28 to equal
strategy +0x1e8, and decision +0x70 to be nonzero. It compares six identity
fields: decision +0x48/+0x4c/+0x50/+0x58/+0x60/+0x68 against identity
+0/+4/+8/+0x10/+0x18/+0x20, then requires the last field also to equal the
strategy value. Failure returns false before the clock update. Success updates
the per-stream clock and a boolean map, returning true.

The bounded direct B/BL caller scan found no call sites for this helper in the
inspected library. This does not rule out indirect/external callers, but means
these checks **cannot yet be attributed to the active presentation path**.
Do not infer that the existing renderer necessarily uses this helper.

The separate `request_render_frame` call-site index does reference
`Clock::computePts`, `AVSyncTiming::isAudioClockFresh`, `decideVideoJoin`,
`shouldHoldVideoForAudio`, `nextVideoDeadline`, and
`CadenceController::reconcileVideoDuration` / `durationBounds`.
This is a useful next path for bounded branch inspection; catch-up/hold log
strings alone do not prove the conditions, thresholds or policy in use.

## Next bounded inspection targets

- `request_render_frame`: `0x1efb48`, 2844 bytes.
- `decideVideoFrame`: `0x1f07c8`, 1584 bytes.
- `commitPresentedFrame`: `0x1f0ef0`, 308 bytes.
- `updateAudioClock`: `0x1f1274`, 292 bytes.

The [synchronization follow-up](yoosee-rtc-sync.md) establishes callers of
`request_render_frame` and `updateAudioClock`, the 500 ms freshness check and
conditional audio-ahead holding. Direct callers of the decision/commit helpers
and concrete hardware-output behavior remain unproven.

Sequential static reads used a 128 MiB memory cap, no swap and 50% of one CPU;
peak observed memory was 31.9 MiB. This checkpoint changes documentation only.
Real authenticated RTC media fixtures, relay ownership, platform verification,
teardown and per-device capability gates remain unresolved independently.
