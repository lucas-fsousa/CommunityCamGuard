# Native archive storage policy —2026-10-29

`Recorder.native_archive()` now supplies a live, server-owned `saving_allowed`
predicate alongside the immutable destination and segment policy. The ordinary
Yoosee driver preserves this predicate in every recovered source. It neither
derives disk policy from camera firmware nor grants a new camera feature.

The existing consumer loop observes pause/resume before processing its next unit.
A transition confirms the old FFmpeg child's exit, retires the browser-output
epoch, discards dependent video/AAC backlog and waits for a fresh configured IDR.
It then creates one replacement child: H264/AAC live output without an archive
while paused, or live output plus a new original-codec archive epoch when resumed.
There is no second capture/account, extra policy thread or concurrent encoder.
Existing player subscribers reconnect to the new epoch; a brief keyframe/setup
gap is expected, not uninterrupted byte delivery during the transition.

An initial pause prevents archive directory preparation and file opening. Queued
AAC is drained while waiting for a new IDR. Invalid policy results stop the source;
unconfirmed child retirement retains ownership and never starts a replacement.
The predicate only reads the recorder's in-memory pause flag, with no database,
network or driver callback. StorageMonitor's existing full/resume hysteresis feeds
that flag; it expresses the requested pause, not a synchronous native-writer ACK.

78 focused storage/delivery/source tests passed under192MiB/no swap/50% CPU
(124.5MiB peak).37 driver/recovery/offer regressions passed (150.8MiB peak).
44 recorder/lifecycle and real local FFmpeg tests passed under256MiB (126.9MiB
peak). The FFmpeg fixtures exercise saving→live-only→saving for60/300s policies,
probe H264/AAC in all three browser outputs, verify the paused phase creates no
recording, and retain both original HEVC/AAC files without overwrite. These use
one-second64x64 generated inputs, not a camera endurance or300s rollover test.
Mypy362 files passed under256MiB (96.7MiB peak); Ruff passed.

No disk was filled, files deleted, camera setting changed or physical action sent.
Native production boot remains disabled. Camera-backed pause/resume acceptance,
durable confirmed-retirement/bootstrap and identity maintenance remain required
before permanent native rollout. This work connects actual storage policy to the
normal source path; it does not certify those remaining lifecycle gates.
