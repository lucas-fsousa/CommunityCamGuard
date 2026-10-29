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
Native production boot remains disabled. Durable confirmed-retirement/bootstrap
and identity maintenance remain required before permanent native rollout. The
camera-backed pause/resume acceptance was subsequently completed below.

## Actual camera3 acceptance — trial U

The initial real trials uncovered a duplicate-video-DTS muxer failure on resume,
not a successful acceptance; see the [cause and repair](native-archive-timestamps.md).
After repair, the ordinary driver completed110s with archive pause at20s and
resume at50s. An isolated, unstarted Recorder supplied its actual archive plan and
public pause/resume methods, so the two ordinary recorders were not paused. This
exercises the native policy consumer, not a deliberately filled disk/global stop.

The authenticated existing player socket independently delivered H264/AAC before,
during and after the pause on source revision2. During the paused phase, all
archive sizes remained identical over8s. Resuming created a distinct original
archive that continued growing after the final player probe. Both1920x1080 HEVC /
16kHz mono AAC files survived confirmed shutdown and rollback: video durations
22.06s/57.20s,335/868 video packets, with strictly increasing DTS. The resumed
archive contains one one-tick adjustment, proving this trial exercised the fixed
collision rather than simply avoiding it. Configured segmentation remained300s;
neither a300s rollover nor continuous multi-day recording is certified here.

The native account stayed running with zero reconnects, both other recorders were
present at every5s observation, and FDs stayed31–32. Confirmed server exit, original
container restoration and directory-owner restoration all passed. The whole group
peaked at230.8MiB/no swap,53.044 CPU seconds over128.449s wall time (768MiB cap,
100% CPU). Independent media observation peaked at50MiB and archive probes24.6MiB.
Artifacts are private under ignored `temp/native-storage-20261029-u`.

This closes bounded camera-backed storage pause/resume acceptance. Durable safe
boot, identity refresh, unconfirmed-loss handling and multiple native routes remain;
no primary native rollout was installed by the operator clone.

## Saved delivery checkpoint

Commit `8da3fc2` passed GitHub CI (5383 tests,4 skipped), all Node contracts and
the image lifecycle smoke. Five additional isolated repetitions of all10 new
storage-policy tests passed under192MiB/no swap/50% CPU (109.2MiB peak).
The bounded local image is `community-cam-guard-app:wsl-8da3fc2`, deployed on WSL
as build `b-3767f63eea2a` with only the app replaced. The previous image remains
tagged `wsl-6e28bcb`. Local authentication/unauthenticated denial, public HTTPS
health/build and three online recording statuses passed. Source policies remain
legacy revision0 and native startup remains inactive. Shared hub and tunnel were
not restarted, with no container OOM or unexpected restart observed.
