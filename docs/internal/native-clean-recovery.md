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
Real clean-session reacquisition and reader reattachment remain to be measured.

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
