# Native media recovery after confirmed retirement

Implementation checkpoint,2026-10-28 local: offline verified; real acceptance
still pending in this section. This does not enable native startup by default.

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
