# Native media recovery after confirmed retirement

Implementation checkpoint,2026-10-29: offline verified, followed by the bounded
real clean-retirement acceptance below. This does not enable native startup by default.

`RecoveringSource` holds one registry camera/domain reservation while sequentially
running driver sources. Only a normal source return certifies completed teardown;
exceptions propagate without constructing a replacement. Existing blocked STOP,
broker-release, TCP-close and child-exit barriers are unchanged. No extra thread,
encoder, camera capture, RTSP fallback or unbounded result queue is created.

The driver classifies retryable returns. Yoosee admits clean socket/media endings,
contention and local session retirement, plus confirmed-closed offline/transport
opening failures. It explicitly refuses protocol/codec/evidence errors, stale or
rejected authentication and any unconfirmed cleanup facts. It does not renew
credentials or infer support from enrollment. Each replacement revalidates the
same exact-device offer before construction, retaining the reviewed motion scope.

Backoff grows2/4/8/16/32/60 seconds and stays capped at60, resetting after a60s
healthy run. Cancellation is checked at most100ms apart during the wait; driver
evidence is checked once per second and again before/after construction. During
the wait, status reports `recovering`/unavailable; subscriptions cannot fall back
to legacy media. Existing readers close and must negotiate a fresh MP4 epoch.

The registered Yoosee source factory now uses this coordinator, but normal service
boot still creates no native activation. The operator A/V trial can opt into
`--recover-at SECONDS`: it stops only the native test session through its normal
protocol cleanup, without power cycling, changing network configuration, resetting
the camera or moving/sounding/illuminating it. This is a controlled clean retirement,
**not** a simulation of an unreachable peer or missing CLOSE acknowledgement.

131 focused recovery/driver/registry/runtime/socket/trial tests passed under192MiB,
no swap,50% CPU, observed157.5MiB peak. Mypy359 source files passed under256MiB.
Tests cover confirmed teardown before replacement, shared-domain exclusion during
backoff, finite memory/history, capped waits, healthy reset, cancellation, revocation,
unconfirmed TCP/STOP/route cleanup and preservation of the current driver grants.
These were initial offline checks; the subsequent real acceptance is below.

## Real camera3 recovery trial L — 2026-10-29

Revision `f0a027a` passed CI5325 tests (3 skipped), Node contracts and the image
workflow before the trial. Under768MiB/no swap/one-CPU quota and a240s hard runtime,
the operator ran90s A/V/archive, requesting a clean session retirement at20s.
No PTZ, light, audio-output, siren, reset/reboot or camera/network configuration
write was performed. Existing shared-hub interruption/rollback was declared.

The same registry owner/source revision2 passed `running → recovering → running`.
The monitor observed one restart at30s and no further restart through90s. Both
other camera recorders stayed active at every10s sample and in the wire client's
status checks. The existing authenticated player alias delivered four binary
messages before recovery (989,024 bytes), then four from a newly attached reader
(951,141 bytes), with H264/AAC MIME in both cases. FFprobe confirmed1920x1080 H264
and16kHz mono AAC; the first2s of both private captures decoded without errors
under192MiB (31.9MiB peak). No browser was launched: this is real socket/codec
acceptance, not new visual browser/end-to-end latency acceptance.

The single capture's original archives retained two distinct UUID epochs:
first HEVC/AAC segment21.966667/22.048s; replacement segments59.866667/59.840s
and4.966667/5.120s. All were HEVC1920x1080/AAC16kHz mono, without overwriting the
first epoch. Private artifacts remain ignored in `temp/native-recovery-20261029-l`
and the camera's UTC archive directory. In-progress recording gaps are expected
during session replacement; this is not gap-free archival continuity.

The trial used186.7MiB peak,32.907 CPU seconds over107.027s total. Descriptors
stayed27–28 through the10s samples, including the restart. Clean service-stop and
production-restored markers exist; original containers resumed without OOM or
unexpected restart. The separately capped shared hub is outside those measurements.

This accepts clean-session recovery/reader reattachment on camera3, not recovery
from an unreachable peer lacking CLOSE/STOP proof. That path still fails closed,
as do revoked evidence and stale/rejected credentials. Permanent startup selection,
token renewal, multiple native routes and endurance remain separate work.

The follow-up classifier also recognizes the worker's existing clean `transport`,
`heartbeat_timeout` and `lifetime` returns, and native resolver deadline/failure
labels. Cleanup facts are still required; unknown resolver cleanup is not retried.
168 focused regressions passed under192MiB/no swap/50% CPU (171.9MiB peak). These
additional failure labels were tested synthetically, not by disrupting the LAN.

## Control admission across reconnect gaps

The Yoosee recovery adapter now retains a separate control-owner lease across the
whole run, including backoff when no session is published. Same-camera PTZ/state
reads report recovering rather than authenticating a legacy route; unsupported
writes remain blocked and background PTZ warmup skips the reserved camera. STOP
remains idempotent. A session for another camera cannot publish over that lease.
The broker socket itself is not held while waiting: if another camera legitimately
uses the shared account between clean epochs, a `busy` attempt is retried with the
same bounded backoff rather than interpreted as permission to race authentication.

A normal fully retired run releases the lease. An exception retains it with the
registry's blocked reservation; no public reset bypass is introduced.97 focused
offline tests passed under192MiB/no swap/50% CPU (142.8MiB peak), including legacy
login/warmup exclusion during backoff, safe STOP and uncertain-exit retention.
Mypy359 source files passed. These control-gap cases did not send physical actions.

### Repeat M and rollout — 2026-10-29

The final adapter (`bf05f93`) passed CI5334 tests (3 skipped), Node contracts and
the image workflow. A second real camera3 trial ran60s, retiring its media session
at20s with the control-owner lease enabled. The wire client again observed
`running → recovering → running` on revision2, four binary messages before
(983,928 bytes) and four after (951,057 bytes), with H264/AAC MIME. The monitor
reported one restart at30s, both other recorders active throughout and27–29 FDs.
Clean service-stop/rollback markers exist. The process group used185.7MiB peak,
24.706 CPU seconds over77.505s total, under768MiB/no swap/one CPU. No camera action
or simulated unreachable-peer failure was sent for this repeat.

The validated runtime was then built with512MiB/no extra swap/50% CPU and deployed
to the WSL app only, build `b-eb39b86f2524`. HTTPS health/build, primary login and
unauthenticated rejection passed; all three cameras were online and their current
recording files grew. Native sources remained inactive and original primary
source policies stayed legacy revision0. No container OOM or unexpected restart
was observed. This ships the recovery implementation, not automatic native startup.
