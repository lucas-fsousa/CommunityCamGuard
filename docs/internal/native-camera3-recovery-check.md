# Camera 3 native recovery checks

## Follow-up: preparation budget retains the real account — 2026-11-08

Code `baa43d9`, image `71307fff8ab2` passed a controlled preparation-expiry check
on camera3. A private operator wrapper hid readiness for the first source attempt
while the real native account connected normally. It did not delay network I/O,
change protocol timeouts, modify the database or exercise a physical control.
This is an injected readiness-notification delay, not a measured slow WAN event.

At the unchanged22s wait budget, the unadmitted source returned busy. After the
normal recovery backoff, its replacement used the SAME actual account and SAME
live thread; the account was not stopped/replaced and no second login occurred.
Ready A/V was observed26.442s from the beginning of checkpoint restore. It then
delivered15seconds of shared MP4:17chunks/5122404bytes, with original recording
active. The01:47Z deployment had still been healthy before this exercise: at
03:38Z all three sources were available/online/recording, camera3 revision261,
with no new failure events or app restart. This was a status/event observation,
not continuous browser/endurance measurement over that whole interval.

The03:42:13.170Z retry event on2026-11-08 reports busy with confirmed unadmitted
source retirement (no camera lease/consumer/decoder). It is not a shared-socket
cleanup receipt. The helper started field RTSP recorders during its wait and left
their policies unchanged. It saved the actual clean restart checkpoint and exited
before normal app startup. Public service handoff was briefly interrupted, as in
the earlier helper checks. Limits:512MiB/no swap/50% CPU/180s; sample86.51MiB.

The normal container resumed at03:42:59.361Z on that image. Startup logged two
additional busy retries at03:43:11.635Z and03:43:14.778Z, then recovered; their
specific trigger was not measured. Subsequent authenticated HD MSE delivered
30seconds/32fragments/9300142bytes. The original archive actually grew during
that observation. Camera3 remained native/active267 and field units legacy/
active22/22; all three were available/online/recording. OOM=false/restarts=0.
CI42443703220 and Image42443703227 passed. This validates this bounded preparation
path, not the original group outage's cause or all native failure modes.

## Follow-up: real account drains before camera binding

Code `616a6d1`, image `55521abc586c` passed the actual readiness-to-binding race
on camera3. Under the existing clean-checkpoint restore, a private operator hook
stopped the first real native account immediately before its first binding call.
The account lifecycle synchronously left `active`; the real binding refused before
any camera lease/child/decoder existed. The new typed refusal returned `busy`
instead of permanently ending the source. This stopped a server transport account,
not the camera: no reboot/reset, speaker/light/motor/settings command was sent.

The retained service admitted one replacement only after the old account reported
confirmed cleanup and its thread had exited. The source recovered through exactly
one replacement, without latching its owner stop flag. Restore-to-ready elapsed
8.693s, including the first connection, injected drain and replacement login;
this is not a measured mid-stream outage duration. It then delivered15seconds of
local shared MP4:17chunks/4731514bytes, with original recording active. The
01:46:29.909Z retry event on2026-11-08 reports busy, lease-detached=true,
consumer-alive=false and cleanup-failed=false. These facts certify refused camera
admission, not a fabricated remote camera CLOSE receipt.

The helper saved the actual clean restart checkpoint and exited before normal
app startup. The field RTSP policies stayed unchanged. Resource limits were
512MiB/no swap/50% CPU/180s, with a171.2MiB sample. No real DB lock, copied proof,
new enrollment, public fault endpoint or full local suite was used. As in the
earlier helper exercise, public dashboard/recording service handoff was briefly
interrupted; this was not an uninterrupted public-app test.

Normal service started2026-11-08T01:47:11.180Z on that image, camera3
native/active261 and garage/yard legacy/active22/22. Authenticated HD MSE delivered
30seconds:32fragments/9450741bytes, with all revisions unchanged during observation
and all three sources available/online/recording afterward. App184.2MiB and
hub214.9MiB in one sample; OOM=false/restarts=0. CI42435567176 passed6240tests/
1skip and all frontend contracts; Image42435567049 passed. Native group/endurance
reliability remains open: this result does not establish the original incident's
cause or recovery from genuinely uncertain account cleanup.

## Follow-up: active validation pause and AV session closure

Code `ac07c7d`, image `28d527a1f00e` passed two further real camera3 exercises.
A private, resource-capped operator process restored its existing clean checkpoint
using the normal driver offer/runtime; no migration, enrollment, proof grant or
camera-setting action was performed. The field units retained their RTSP intents.
The public app was stopped during this exercise; it was not a fault injected into
an uninterrupted public dashboard. Original recording was enabled and field
recorders used the existing hub. Normal service handoff caused brief interruption.

The first fault was an in-memory `SourceValidationBusy` at the actual outer
offer guard, not a production SQLite lock. The real camera lease detached and
consumer exited. During a further two-second pause there was no replacement,
the owner stop flag remained clear, and its completion remained pending. After
fresh validation was allowed, the next child appeared after5.413s measured from
the pause's start, including retirement and the intentional hold. Shared MP4
output delivered8chunks/2392362bytes during the following8seconds.

The second fault called only the active camera3 AV session's `stop()`; it did
not stop the owner, reset/reboot the camera or send physical controls. A new
child appeared after4.114s and delivered7chunks/1775865bytes over8seconds.
Both replacements retained the SAME account UDP socket and native policy
revision. Source recording was active afterward. Events at00:53:25.003Z and
00:53:37.360Z on2026-11-08 confirm lease detach, inner UDP CLOSE receipt and
broker release, with consumer_alive=false/cleanup_failed=false. The helper then
saved a real clean restart checkpoint and exited. These results are not evidence
for absent broker receipts, group outages or indefinite native endurance.

The helper was capped at512MiB/no swap/50% CPU/180s; one sample was88.25MiB.
The normal container resumed at00:54:39.689Z, camera3 native/active255 and field
units legacy/active22/22. The corrected image then delivered30seconds of
authenticated HD MSE:32fragments/9535893bytes, without source revision change.
All three were available, online and recording; OOM=false/restarts=0.
CI42431693157 passed6235tests/1skip and frontend contracts; Image42431693070
passed. No GUI, emulator or full local test suite was launched.

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
