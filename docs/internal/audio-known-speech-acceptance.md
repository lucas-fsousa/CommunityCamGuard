# Camera3 known-speech acceptance — 2026-10-20

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

| Known reference mode | Application result | Native packets | Late packets | Maximum lateness | Maximum send call | Released |
| --- | --- | --- | --- | --- | --- | --- |
| Message | HTTP200, completed,10s | 1000 | 12 | 126.8ms | 216.7ms | true |
| Stream | complete, completed,10s | 1000 | 32 | 268.8ms | 348.8ms | true |

Both report500 canonical frames sent. Operators peaked45.9/44.2MiB with zero swap
under192MiB/50%-CPU limits. Reference generation used23.6MiB/no swap. These are
server-side timing observations, not proof of actual speaker underflow or its cause.
No pacing/buffer changes were made based on these measurements.

Physical intelligibility and clean audible stopping for each known-reference mode
were requested asynchronously and remain pending. No full-duplex, continuous soak,
or new all-model homologation is claimed. Reset/re-enrollment is a separate later
test so it does not invalidate the current audio/native-stream test setup.
