# Decoder input: scheduling delay is not pipe backpressure — 2026-11-13

## Observation

The installed lost-ACK correction remained active. At21:36Z, the app reported
camera3 native/active279, field RTSP22/22, all three online/recording; app started
2026-11-11T08:49:16Z, OOM=false/restarts=0. Recent history includes natural
account_ended events with local socket closed and broker release unacknowledged,
and a2026-11-13T17:40:43Z consumer_failed/TimeoutError/stdin_timeout at pump:77.
There was no later terminal source event. A pre-deployment15s authenticated HD
check delivered17fragments/5988478bytes with unchanged source revisions.
This is current delivery plus event history, not a continuous multi-day video audit.

No pipe-readiness sample was recorded at the17:40 incident. Do not assert that
this particular timeout was false or that every historical stall shares its cause.

## Reproduced defect and correction

The writer checked its one-second deadline BEFORE attempting its nonblocking
write. A consumer scheduling pause could therefore kill a healthy decoder even
when the pipe had space. Two failing-before-fix regressions use real OS pipes:
a delayed first pump and a delayed pump after partial progress both rejected
writable input. No camera, load generator or mocked pipe readiness was involved.

The writer now attempts one existing16KiB nonblocking write before evaluating
the no-progress deadline. Successful writes refresh the SAME one-second deadline
using their completion time; failed/would-block/interrupted attempts still expire.
There is no larger buffer, timeout increase, busy retry loop, new worker, frame
drop, interleaved access unit or new FFmpeg process. The independent decoder
frame-progress watchdog and all existing process cleanup barriers stay unchanged.

The old negative partial-progress test drained the pipe just before expecting a
stall, encoding the elapsed-only bug. It now leaves the real pipe full before
advancing the clock; that genuinely blocked input still times out. The added
completion-time case also proves that descheduling during a successful write does
not backdate the next deadline. Byte-for-byte frame order remains covered.

96 focused writer/consumer/decoder/archive tests passed, plus Ruff/Mypy. Tests
were capped at192MiB/no swap/25% CPU, peak128.4MiB; Mypy separately capped192MiB.
No broad local suite, browser/emulator, physical camera command, recording
deletion or unrelated process termination was used.
