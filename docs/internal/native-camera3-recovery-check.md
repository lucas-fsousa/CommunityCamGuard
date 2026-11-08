# Camera 3 native decoder-failure recovery — 2026-11-07 local date

## Scope and actual outcome

Camera 3 alone returned to native LAN UDP on code `1afcf37`, image
`2a4e370c9c71`, build `b-7d0eea8a5b49`. Garage and yard retained active legacy
RTSP policies, revisions22/22. The camera3 policy stayed native/active243
throughout the injected failure. This is not three-camera native acceptance or
proof that every cause of the earlier outage is resolved.

The operator stopped and verified the app/hub namespaces before handoff, saved
an owner-only database/config backup under ignored
`data/native-cam3-recovery-20261107-v1`, and used the existing retirement/activation
coordinators under ServerOwner. Fresh read-only properties matched the existing
exact-identity HEVC/LAN-UDP/original-A/V proofs; no new feature proof was granted
or copied from another unit. A paused-archive bootstrap produced a clean restart
checkpoint, then the normal app resumed the source and original archive.

Preparing the handoff briefly interrupted all video/recordings. The later fault
injection affected only camera3's local decoder, not either field source. No PTZ,
camera setting, speaker, light, siren, reboot, reset or onboarding action was sent.

## Actual fault, not just another startup window

The probe first received15seconds of authenticated HD MSE:17binary fragments,
5143264bytes. It then matched exactly ONE app-child FFmpeg by comm, parent PID1
and camera3's archive-output argument before sending that PID SIGTERM. No broad
process kill or command-line dump was used. The private fault manifest records
the interrupted archive path without publishing credentials or packets.

The original decoder PID25 was replaced by PID70 after3.515seconds, while the
owning account UDP socket inode remained unchanged. A new MSE connection then
received30seconds continuously:32binary fragments/10566682bytes. App start time,
source revisions and modes did not change. All three sources remained available,
online and recording. The injected event at2026-11-08T00:09:01.054Z recorded
confirmed lease/UDP detach, inner CLOSE receipt and broker release; the old
consumer was no longer alive and cleanup_failed=false.

The interrupted original was independently probed:233.347seconds,1920×1080HEVC,
16kHz monoAAC. The next original subsequently grew by262144bytes in a later
10second observation. An initial3second size sample showed no increase; do not
misreport that alone as a stall. Growth on this low-rate original appeared in
batches. No footage was deleted, relocated or re-encoded for the check.

The observer peaked at52MiB, zero swap, under192MiB/25%-CPU/150s limits. The
single-source bootstrap used a512MiB/no-swap/50%-CPU container and a180s deadline.
No browser/emulator or full local test suite was launched.

## Real diagnostic defect exposed by the test

The SIGTERM event was labelled `Mp4OrderError`, expected moof/actual mfra. This
was the local FFmpeg's terminal seek index, not evidence of a corrupt camera
packet. FFmpeg documents the `skip_trailer` MOV flag for omitting mfra/tfra/mfro
from fragmented output: [official muxer options](https://ffmpeg.org/ffmpeg-formats.html#Options-9).
The installed FFmpeg's own muxer help also confirmed support.

The live-only decoder command now includes `+skip_trailer`. Original archive
segment options are unchanged, retaining the final random-access index. The
strict live parser and its memory/order limits are not weakened. A real bounded
FFmpeg regression proves that the former command emits a rejected mfra, the
new command feeds complete initialization/media pairs successfully, and the
original archive still ends in mfro with the original codecs/timestamps.
Seventy targeted tests, Ruff/Mypy passed under resource caps; this is separate
from the live baseline result above.

## Post-deployment repeat — passed

Code `e0c7bed`, image `e321897153c6`, build `b-0f412f7d8a1d` replaced only the
app at2026-11-08T00:20:11.802Z. CI42429008509 passed6229tests/1skip and all Node
contracts; Image42429008498 passed. Camera3 resumed native/active246; garage/yard
remained legacy/active22/22. The restart was an explicit deployment, not automatic
failure recovery. No protocol profiles or field-source intents changed.

The same bounded probe again sent SIGTERM only to the uniquely matched camera3
decoder, PID38. PID59 replaced it after3.006seconds, on the SAME UDP socket.
An already attached MSE viewer received close1013 instead of silently hanging;
a replacement connection delivered30seconds of media. Counts were17fragments/
5321993bytes in the15seconds before the fault and32fragments/10056781bytes after.
App start time, source modes/revisions stayed unchanged throughout the injection.
All three sources were available, online and recording afterward.

The event at00:21:38.304Z now correctly identified DecoderExitedError/returncode255,
not Mp4OrderError. Lease detach, UDP CLOSE receipt and broker release were confirmed;
consumer_alive=false/cleanup_failed=false. The interrupted original independently
probed as81.876seconds,1920×1080HEVC plus16kHz monoAAC, and its final mfro index
was retained. Thus skip_trailer affected only the live pipe, not the archive.

Observer peak54MiB/zero swap,1.025s CPU or less in the measured run; app182.5MiB
and hub25.69MiB in the final sample, each under its existing1GiB ceiling.
OOM=false/restarts=0. No browser, decoded-frame audit or absolute live-latency
measurement was performed. This is measured server delivery and local fault
recovery, not multi-day/group endurance certification.

## Reproduction notes and limits

The initial observer advertised video only, so the native A/V route correctly
closed1008 before any injection. It was corrected to advertise H264 and AAC;
the production A/V source requires both. No speaker output was requested.
An initial direct script invocation also lacked the repository module path and
failed before I/O; the successful run used `python -m temp.probe_camera3_recovery_20261107`.
Private operator scripts and backup manifests stay ignored, not public endpoints.

This proves automatic recovery from ONE actual local decoder termination on the
test camera. It does not prove recovery from missing broker receipts, unknown
physical close, long WAN outage, simultaneous field failures or multi-day native
endurance. Group shutdown/SQLite contention fixes retain their separate synthetic
and real-local-SQLite evidence. Do not migrate the field units solely from this
result, and do not re-open already completed physical controls tests.
