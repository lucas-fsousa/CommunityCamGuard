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

## Deployment and actual delivery

Code29b289a, image01353ecc9d0c/tag `wsl-decoder-poll`, buildb-9bd59f4d3f94.
CI43513071311 passed6253tests/1skip and all frontend contracts;
Image43513071412 passed. Build used a clean tracked-files context (2.943MB),
384MiB/no swap/25% CPU; no APK, footage or private environment files entered it.

Only the app container was replaced, starting2026-11-13T21:45:25.601Z. Camera3
resumed native/active282; field units stayed legacy RTSP/active22/22. A30s
authenticated HD MSE check delivered32fragments/13939633bytes without source
revision change. All three were available/online/recording, and archive files
actually grew for EACH camera during the observation, not just a process flag.
OOM=false/restarts=0. No camera settings, physical commands or test fault was sent.

Before deployment there were exactly3FFmpeg processes inside the app (one native
pipeline and two legacy recorders). No extra encoder was created for this reader.
Other WSL projects were neither stopped nor tuned. The field units remain on RTSP;
this backend correction does not grant native capabilities or migrate cameras.
