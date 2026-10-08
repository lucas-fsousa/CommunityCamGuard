# RTC receive audio: sample clock and Android callback

2026-10-08, offline follow-up to [synchronization](yoosee-rtc-sync.md).
All native addresses refer to the pinned SDK 6.45 ARM64 `libgwplayer.so` hash
recorded in [render evidence](yoosee-rtc-render.md).
This is **received audio playback in the manufacturer app**, not the camera's
speaker/two-way-audio send protocol.

## Clock production before the virtual position method

`AudioRenderer::run` (`0x183278`, 5264 bytes) reads the queued AVFrame PTS
at +0x88 and duration at +0x1d8, scales both by d11 and converts them to integers
(`0x183670–0x1836a0`). This supplies x26/x25 for downstream paths; the d11
producer is still a separate tracing target.

On the filtered-output path, `AudioFilter::receiveSamples` at `0x1838e4`
returns a positive count before the renderer proceeds. With a valid accumulator
and positive denominator, it computes an increment as
`count * 1,000,000 / denominator * rate`, truncated to an integer
(`0x183928–0x183944`), then atomically adds it to renderer +0xd8. The helper at
`0x33b670` is an atomic fetch-add: LSE `ldaddal` or an acquire/release exclusive
loop. The caller adds the increment to the returned old value to obtain the
end position (`0x183948–0x183950`).

The denominator comes from the product of configuration fields at stack
+0xf0/+0xf8 and the signed byte-width conversion of +0xf4
(`0x1837b4–0x1837e8`). Do not label each field or the count's unit solely from
the arithmetic or `receiveSamples` name until their producers are established.
Another path applies corresponding arithmetic at `0x184470–0x1844bc`.
A reset branch explicitly replaces the accumulator from x26 at `0x183da0`.

These paths explain a software-maintained position estimate. They do not query
a hardware playback head, prove that all bytes were accepted, or prove sound
was emitted. The already-resolved `computeRenderPosition` slot scales this
position and is not a hidden device-latency correction.

## Native callback boundary

The renderer's virtual slot +0x20 resolves, via vtable ABS64 relocations, to:

| Renderer | Relocation | Method |
| --- | --- | --- |
| Abstract | `0x34c9d8` | `__cxa_pure_virtual` |
| Empty | `0x34f700` | `EmptyAudioRenderer::frameUpdate`, `0x1f7860` |
| Android JNI | `0x355a30` | `GWAudioRendererJni::frameUpdate`, `0x2bfb4c` |

The positive filtered count is passed to this slot at `0x183964–0x183970`.
The JNI implementation (636 bytes) checks the Java object/method references,
JNI environment and positive size, then prepares a Java array through JNI
table calls, optionally invokes a listener and calls `CallVoidMethod` at
`0x2bfd08`. No playback-completion result is returned from this callback.
Environment/reference/size failures take logging and return paths.

The constructor resolves **`onFrameUpdate` with descriptor `([B)V`** at
`0x2bf7a8–0x2bf7bc`, storing the method ID at object +0x20. This independently
pins the callback as a byte-array/void method; do not infer its signature from
a differently sourced Java class.

## Existing Java decompilation is not matching evidence

The existing ignored `re/decompiled/sources/com/gw/player/render/IAudioRender.java`
declares `onFrameUpdate(AVData)`, and its `DefaultAudioRenderer.java` implements
that signature. It cannot establish the implementation of this SDK's `([B)V`
callback. Its method also carries a decompiler correctness warning. No behavior
from that Java file was used to claim Android write/latency semantics here.

Next Android-side inspection must locate the matching class in the corresponding
6.45 base APK and pin its provenance before tracing the callback. Do not launch
a whole-APK decompilation to resolve this small boundary: use a bounded,
single-class extraction with an enforced memory limit when available.

## Scope and validation

Static reads were sequential with 128 MiB cap, no swap, 50% of one CPU. No
proprietary code execution, camera command, network capture or production
change. The queue/vtable checkpoint `5afa3b9` passed CI `37834577611`.

Physical camera 3 is authorized for future announced tests, but this app-side
receive path cannot be validated by sending a siren/light/PTZ command. The
remaining native-media gate still needs authenticated media/session provenance,
platform verification and safe teardown; physical confirmation alone does not
replace those requirements.
