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
The caller-provided limit is derived from frame duration, as traced below.

The initial-join helper (`0x1f45bc`, 28 bytes) similarly checks its freshness
boolean, positive limit and video-ahead delta. Although its caller computes a
queue-size boolean in `w4`, this helper body does not read `w4`. Do not infer a
queue-size-dependent decision from its signature alone.

### Caller-provided limit is frame-derived, not a fixed network timeout

Both inspected `VideoRenderer::obtainFrame` call sites initialize the reference
argument from `trunc(renderer[+0x128] * 1,000,000 * AVFrame[+0x1d8])`:
`0x245818–0x24584c` stores it at stack +0x118, and `0x245a4c–0x245a7c` at +0xf8.
Those exact addresses are passed by reference to `request_render_frame`.

The second path applies a fallback **only when that integer is zero and the
configured positive frame rate exists**: `min(trunc(1,000,000 / fps), 100,000)`
at `0x245a80–0x245ac0`. It does not replace an arbitrary negative duration or
cap every nonzero duration at 100 ms. Do not interpret this fallback cap as a
universal buffer size or live latency target.

The renderer constructor copies configuration +0x10 to renderer +0x128 and
configuration +0x0c to renderer +0x124 (`0x241304–0x241310`). In
`start_render_nolock`, frame metadata +0x160 supplies configuration +0x10
(`0x1e7378–0x1e737c`), connecting it to the previously traced time-base scalar.
The frame-rate ratio is computed at `0x1e735c–0x1e7394`. The existing-renderer
branch copies this updated configuration back at `0x1e7454–0x1e745c`.
Thus the inspected hold input is a media-duration conversion, not a hardcoded
500 ms wait; 500 ms belongs to the separate audio-clock freshness check.

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
the pinned vtable follow-up below resolves this particular method. It does not
establish how the renderer originally obtains the PTS argument.

## Queue ownership and the meaning of the boolean

`SafeQueue<Frame>::front(bool)` (`0x1ec12c`, 352 bytes) locks the queue. With
false and an empty queue (+0x28 == 0), it returns an empty frame reference;
otherwise it calls `front_locked`. That helper (`0x1779c0`, 1208 bytes) waits on
a condition variable while empty (`0x177a34–0x177a50`), checking a queue-state
counter after wakeup. The nonempty branch copies the head's retained frame
reference and metadata (`0x177b6c–0x177c58`) without consuming it.
Thus false means no empty-queue wait here, not “do not remove the frame”. It
still takes a mutex and must not be described as lock-free/nonblocking.

The distinction is explicit in `popFront(bool)` (`0x176d9c`, 388 bytes): after
the same lookup, it calls `pop_locked` only if the returned frame pointer is
non-null (`0x176e90–0x176ea0`). The earlier decoder's packet specialization is
not automatically proven by this frame-specialization inspection.

`request_render_frame` uses `front(false)` at `0x1efb9c`. In the first observed
`obtainFrame` caller, a zero decision exits at `0x245d20`; a nonzero decision
advances a frame timestamp by its duration, then explicitly calls queue `pop`
at `0x2458a8`. The other caller has further validation/filtering paths and a
separate pop at `0x245cd8`, so do not generalize immediate consumption to every
successful decision. A scheduling decision is not proof of display completion.

## Audio virtual slot: no hidden hardware-latency adjustment here

ELF ABS64 relocations at the following primary vtable +0x10+0x58 slots all
resolve to `AbstractAudioRenderer::computeRenderPosition` (`0x182c78`):

| Vtable | Slot relocation |
| --- | --- |
| `AbstractAudioRenderer`, `0x34c9a8` | `0x34ca10` |
| `EmptyAudioRenderer`, `0x34f6d0` | `0x34f738` |
| `GWAudioRendererJni`, `0x355a00` | `0x355a68` |

The complete 16-byte implementation converts integer argument x1 to double,
multiplies it by d0, converts back to integer and returns. It does not read the
float argument, query an Android playback position or subtract a latency.
Both already-inspected renderer call sites pass d0 = 1.0 before updating the
audio clock. This is an identity scale subject to floating-point precision,
not evidence of a hardware playback timestamp. The original x1 producer and
which renderer instance a real session selects remain separate questions.

## Validation and remaining gates

Sequential static reads: 128 MiB cap, zero swap, 50% of one CPU, peak 40.5 MiB.
No SDK binary was executed, no camera was contacted, and no production settings
were changed. These are evidence/docs changes, not an implementation of the SDK
scheduler or a diagnosis of the browser's stalls.

The [audio-output follow-up](yoosee-rtc-audio-output.md) traces the software
position accumulator and byte-array JNI callback, and records a signature
mismatch that prevents reusing the existing Java decompilation as proof.

Next: finish audio field/scale provenance and the remaining successful-request
consumption branches. Real authenticated
RTC fixtures, ownership, platform verification and teardown remain independent
requirements before enabling native streaming.
