# Yoosee RTC: reachable synchronization path

Offline evidence, 2026-10-08; continuation of the
[render boundary](yoosee-rtc-render.md). Same pinned ARM64 APK and `libgwplayer.so`.
Static reachability does not prove a particular live session exercised a branch.
No runtime behavior or camera capability is enabled by this checkpoint.

## Callers, not just exported helpers

The bounded B/BL scan includes same-library PLT stubs:

| Caller | Sites | Callee |
| --- | --- | --- |
| `VideoRenderer::obtainFrame` | `0x24586c`, `0x245b40` | `request_render_frame` (`0x1efb48`, via `0x343020`) |
| `AudioRenderer::run` | `0x1839bc`, `0x184360` | `updateAudioClock` (`0x1f1274`, via `0x3407f0`) |

Targeted instruction windows confirm these calls and the video caller's test of
the return value. Neither `decideVideoFrame` nor `commitPresentedFrame` has a
direct caller in this scan. Their exported presence must not be substituted for
the proven `request_render_frame` path; indirect/external callers remain possible.

## Monotonic clock and units

`libgwplayer.so` explicitly depends on `libgwbase.so`. In the same archive:

- `libgwbase.so` SHA-256:
  `8b6be4bdcdaa0dbf1ef12046f348500dc803198bc84bb7d6e6674f12d3465726`.
  `tick::steadyTime` (`0xa9a14`, four bytes) tail-calls
  `std::__ndk1::chrono::steady_clock::now`.
- `libc++_shared.so` SHA-256:
  `6e247a17a78e22060dfa2212b2dd5b5b49e643ea1a48e815b3570ad6e9b94d9d`.
  `steady_clock::now` (`0x5313c`, 120 bytes) calls `clock_gettime` with clock ID 1
  and returns `seconds * 1,000,000,000 + nanoseconds` (`0x53184–0x5318c`).
  Its error branch explicitly names `CLOCK_MONOTONIC`.

The multiply-high/shift sequence using `0x20c49ba5e353f7cf` divides signed
nanoseconds by 1000. It appears in `Clock::computePts`, `updateAudioClock`
(`0x1f1344–0x1f1368`) and `request_render_frame` (`0x1efc6c–0x1efc9c`).
These local anchors are therefore **monotonic microseconds**, not Unix/UTC or
the camera's wall-clock overlay. This resolves the earlier clock-unit question;
it does not make raw RTC PTS an absolute monotonic timestamp.

## Audio freshness and holding video

`AVSyncTiming::isAudioClockFresh(now, updated)` (`0x1f4580`, 40 bytes) returns
true only when `updated > 0`, `now >= updated`, and
`now - updated < 500001`: up to **500,000 microseconds inclusive**. Future,
uninitialized and older anchors fail this check. This is clock freshness, not
token expiry, an unconditional 500 ms video delay or a network timeout.

The strategy-name table at `0x34f198`, resolved through ELF RELATIVE relocations,
maps numeric types 0/1/2 to `UNSPECIFIED` / `SYNC` / `NO_SYNC`. Thus setup's
previously unnamed default type 1 is `SYNC` in this binary.

In the inspected video branch, `request_render_frame` checks freshness at
`0x1f0194–0x1f01a8`. At `0x1f025c–0x1f0270`, `NO_SYNC`, stale/unavailable audio,
or a nonpositive limit bypass the audio-ahead hold check. Otherwise it computes
the audio clock and calls `shouldHoldVideoForAudio` at `0x1f0290`.
That helper (`0x1f45a8`, 20 bytes) requires a positive limit and
`video_pts - audio_pts > limit`. The hold branch records per-stream state,
logs and returns zero (`0x1f04a4–0x1f04d0`), which the video caller checks.
The source/configuration of the caller-provided limit remains to trace.

The initial-join helper (`0x1f45bc`, 28 bytes) similarly checks its freshness
boolean, positive limit and video-ahead delta. Although its caller computes a
queue-size boolean in `w4`, this helper body does not read `w4`. Do not infer a
queue-size-dependent decision from its signature alone.

## Audio update boundary

`updateAudioClock` rejects the signed missing-PTS sentinel before locking.
When an existing anchor is positive, it prevents the stored PTS from moving
backwards if both supplied nonnegative identity fields match the stored pair;
it also retains this clamp when either pair has a negative/unknown field.
A fully specified changed pair bypasses that clamp. This is not an unconditional
global monotonicity rule across playback generations.

It clamps the supplied extent to nonnegative, bounds the supplied rate with
`fmaxnm(rate, 0.001)` (constant at `0xf7710`), stores a valid identity pair and
refreshes the local monotonic anchor. Both observed audio renderer call sites
obtain the PTS argument through a virtual method at slot +0x58 first; the
concrete output-device implementation and latency correction are not yet proven.

## Validation and remaining gates

Sequential static reads: 128 MiB cap, zero swap, 50% of one CPU, peak 40.5 MiB.
No SDK binary was executed, no camera was contacted, and no production settings
were changed. These are evidence/docs changes, not an implementation of the SDK
scheduler or a diagnosis of the browser's stalls.

Next: establish the concrete audio-output +0x58 method, caller-provided hold
limit and frame-queue consumption after a successful request. Real authenticated
RTC fixtures, ownership, platform verification and teardown remain independent
requirements before enabling native streaming.
