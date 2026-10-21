# Camera3 known-speech acceptance — 2026-10-20

## Current acceptance: passed with a startup-gap caveat

The owner physically accepted the recorded message, then the split-timing stream
retest: "audio teve um pequeno picote bem no inicio, mas o restante foi sem picotes.
Teste OK!" The subsequent browser microphone/hold-to-speak check was also confirmed:
"pressionar par falar funcionando OK". All three acceptance cases are complete.
Keep the small initial stream gap as a non-blocking improvement. Do not infer
gap-free playback, full-duplex or long-running reliability. No further camera
command was issued to record these confirmations. Earlier failed/partial tests
below are retained as historical evidence, not current acceptance blockers.

Latest owner instruction: physical validations authorized again. The known-reference
message was repeated on camera3 at50% volume: HTTP200, completed=true,
duration_ms=10000, route_released=true. Operator peak44.1MiB/zero swap.
The initial invocation failed with HTTPStatusError before volume verification/audio
submission; status was not retained. A second explicit attempt completed as above.
The owner confirmed the recorded message physically: "Validado ok, funcionou como
esperado." Known-reference message acceptance is now passed, not just transport.
Nighttime IR and reset/re-enrollment remain separate validations; no reset was sent.

Following message acceptance, the paced WebSocket stream was repeated with the
same10-second reference and verified50% volume. First attempt returned409 on
speaker_volume before audio submission. One explicit retry returned type=complete,
completed=true, duration_ms=10000, route_released=true; operator peak44.4MiB,
zero swap. The owner heard the audio, reported intermittent gaps and confirmed it
stopped by itself. That attempt did not pass audible quality; the later accepted
retest is documented below. This scripted test does not exercise browser microphone
capture or the hold-button UI, which the owner subsequently checked separately.

The retained runtime timing summary for the recent stream reports1000 packets,
26 late packets,169.1ms maximum lateness,248.7ms maximum send-call duration,
10707.2ms elapsed, opened/released=true. These are local timings, not speaker-buffer
telemetry. No matching recorded-message summary was available in the inspected
three-hour log window, so this is not a measured message-versus-stream comparison.
The synchronous OPEN refresh inside send_pcm (every4s) is one potential source:
send-call timing includes that response wait, while preloading covers only100ms.
Upstream arrival and scheduling can also consume that lead. Separate refresh/write
timings before attributing the gaps or changing buffering. No further sound sent
after this feedback; no codec, pacing or buffer changes made on this evidence alone.

Sound tests were explicitly authorized again. Only camera3 was targeted; speaker
volume was independently checked at50% before each attempt. No siren, reboot or
reset was sent. Existing WSL services continued running.

## Reference and procedure

The first ten-second message reused an old recording. The user heard output but
could not compare unintelligible/unknown source content; this is **not** an
intelligibility pass. A subsequent old-sample stream attempt returned an error;
its public detail was not retained, so the cause is unknown. Do not claim all
repeated attempts passed.

A new synthetic reference was generated locally using FFmpeg's existing flite/slt
voice, mono16kHz signed16-bit PCM, padded with silence to exactly10 seconds:

> Camera three. Audio test. One, two, three, four, five. End of test.

The reference remains in ignored `temp/camera3-known-speech-20261020.wav` and was
linked to the user for comparison. The local operator sends500 canonical20ms frames
through either the production message HTTP route or the authenticated stream WebSocket.
The latter reproduces paced PCM delivery but does not test microphone capture/UI.
No independent P2P session or unproven transport fallback was used.

## Results

### Split-timing stream retest after dbd6cb4

One authorized camera3 stream at verified50% volume completed10s and released
the route. Operator peak52MiB/zero swap. Runtime summary:1000 packets,11 late,
111.6ms maximum lateness,192.0ms maximum combined send,10734.5ms elapsed;
2 refreshes/0 failures/max192.0ms;1000 writes/0 failures/max2.4ms.
This identifies synchronous watchdog refresh as a measured long blocking operation
in this run, exceeding the100ms nominal prebuffer. It does not prove that every
audible gap comes from refresh or exclude upstream/scheduler delay. No timing or
buffer adjustment was applied. The owner accepted this retest with one small initial
gap and no further gaps. The measured refresh delay is not proof of the cause of
that initial gap.

| Known reference mode | Application result | Native packets | Late packets | Maximum lateness | Maximum send call | Released |
| --- | --- | --- | --- | --- | --- | --- |
| Message | HTTP200, completed,10s | 1000 | 12 | 126.8ms | 216.7ms | true |
| Stream | complete, completed,10s | 1000 | 32 | 268.8ms | 348.8ms | true |

Both report500 canonical frames sent. Operators peaked45.9/44.2MiB with zero swap
under192MiB/50%-CPU limits. Reference generation used23.6MiB/no swap. These are
server-side timing observations, not proof of actual speaker underflow or its cause.
No pacing/buffer changes were made based on these measurements.

Physical acceptance of message, stream and browser hold-to-speak is confirmed by
the owner, with the startup-gap caveat above. No full-duplex, continuous soak,
or new all-model homologation is claimed. Reset/re-enrollment is a separate later
test so it does not invalidate the current audio/native-stream test setup.
