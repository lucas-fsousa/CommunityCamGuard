# Native permanent-block correction — 2026-11-08

## Delivered defect

Final installed code `3a9b58e`, image `5d79a1f06e51`, build `b-62a15acf3206`.
The real counterfactual below used the core correction on `b581416`; the final
guard additionally requires sock.close() to RETURN before publishing local-close
proof, including when it raises an unexpected non-OSError.177focused tests passed.

Code `b581416`, image `f6fa6fc3f80b` fixes a reproduced permanent recovery block:
loss of ONE positive B9 route-close receipt poisoned the entire account even
after its socket and thread had exited. This is a causal before/after comparison
using the real test camera, not an inference from another short healthy startup.
It does not establish why every earlier media/CPU stall began.

The earlier implementation conflated two different facts: remote transport ACK
and local ownership retirement. The mapped SDK ACK callback records delivery
timeout separately from semantic remote resource release; see
[SDK receipt mapping](native-tcp-cleanup-checkpoint.md).

## New retirement contract

Same-account route reuse still requires its correlated positive B9 receipt.
Without that receipt, the old inbox closes and the whole account retires.
Only actual broker socket close, TCP physical close/UDP detach, and the existing
motor STOP/siren OFF cleanup barriers permit the new `account_retired` outcome.
It reports broker_release=false and local broker_socket_closed=true: no ACK is
forged. Every source must join its old consumer/decoder before retry, and the
retained service must observe account-thread exit before another account starts.

Negative B9 status/error ACK, failed socket close, unknown opener/worker exception,
live consumer, decoder cleanup failure or unsafe physical-control cleanup still
fence admission. Legacy/standalone paths keep their previous receipt contracts.
There are no new timeouts, wire fields, camera-count limits or action replays.

## Real counterfactual

The public app was stopped and its PID namespace verified retired. Owner-only
SQLite backups under ignored `data/native-ack-loss-20261108-v1` supplied separate
baseline/fixed copies, with the same existing camera3 proofs and clean checkpoint.
Production intents/proofs were not edited or promoted from these copies.
Only camera3 ran native; field policies remained legacy RTSP. Field recorders ran
inside each helper, with process-handoff gaps. Public dashboard interruption for
the comparison was approximately six minutes; this was not a zero-downtime test.

An in-process operator hook discarded only the actual valid positive B9 receipt
for the first closing camera3 route, after exact peer/session/sequence/generation,
length/checksum/type/status checks. It never forged a response or dropped another
camera's traffic, heartbeat or later route receipt. Same runtime/driver/AV/archive
path as the normal image; no public fault endpoint, emulator or vendor-app proxy.

| Exercise | Actual result |
| --- | --- |
| Old image71307fff8ab2, AV stop + one lost ACK | Permanent blocked source; SourceCleanupUnconfirmed; account thread exited and actual UDP inode absent. |
| Fixed image, same AV stop + one lost ACK | New child after5.719s;20s delivery,20chunks/6160272bytes; original recording active. |
| Fixed image, old-session media loss + lost ACK | media_idle_timeout; new child after10.289s;20s delivery,19chunks/5415028bytes; recording active. |
| Fixed image, paused local FFmpeg + lost ACK | TimeoutError/progress_timeout; new child after12.244s;20s delivery,20chunks/5859144bytes; recording active. |

Replacement times measure child birth, not first rendered frame or absolute live
latency. All fixed cases observed confirmed old account/thread/socket retirement,
joined consumer/decoder, broker_release=false and one source replacement without
restarting that helper process or manually resetting its player/source.

The last exercise stopped exactly one camera3 child FFmpeg with SIGSTOP, without
load generation. Its old PID was reaped. Its final script assertion initially
expected stdin_timeout, but progress_timeout fired first. That classification
assertion failed AFTER the recovery/media/cleanup checks had passed. Persisted
events independently confirm consumer_failed/TimeoutError/progress_timeout and
the closed-account/absent-ACK facts; the helper's normal cleanup also succeeded.
The operator assertion now accepts either watchdog category. This is not reported
as an entirely successful original script run or as a reproduced stdin timeout.

No sound/light/PTZ, camera reset/reboot/configuration, credential renewal or
feature grant was sent. AV setup/teardown only. Existing footage was not deleted,
relocated or re-encoded. Each helper was capped at512MiB/no swap/50% CPU/180s;
observed samples ranged87–170MiB. No GUI/emulator or full local test suite ran.

## Normal deployment

App resumed2026-11-08T04:11:25.162Z, native camera3 active270 and field RTSP
active22/22. Authenticated HD MSE then delivered30seconds/32fragments/9271861bytes
without source revision change. The original archive actually grew; all three
sources were available, online and recording. OOM=false/restarts=0.
CI42445238042 and Image42445238052 passed;176focused local tests/Ruff/Mypy passed
under resource caps, including negative ACK, failed TCP/broker close, unknown
opening, unsafe siren, TCP/UDP and real account/lifecycle fixtures.

The final guarded image resumed at04:17:04.920Z, camera3 native/active273 and field
RTSP22/22. Its30s authenticated HD check delivered31fragments/9400733bytes with
unchanged revisions and actual original archive growth; all three online/recording.
App184.3MiB/hub195.6MiB in one sample, OOM=false/restarts=0. CI42446009763 passed
6250tests/1skip plus all frontend contracts; Image42446009893 passed.

The delivered acceptance is recovery of this reproduced permanent-block class.
It is not a guarantee against every network outage, a three-camera native field
migration or proof of the initial cause of every historical decoder stall. Do not
add an arbitrary endurance gate to this already tested ACK-loss correction.
