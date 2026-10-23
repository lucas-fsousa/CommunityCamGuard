# Recording-consumer retirement — 2026-10-23

Status: implemented and tested with fake recording processes. Production camera
sources and containers were not changed. This completes the recorder admission
part of the [source handoff](go2rtc-source-retirement.md), not upstream retirement.

## Why the existing stop paths were insufficient

Camera removal and bulk pause/stop escalated to `kill` without waiting afterward.
The RSS watchdog waited, but discarded the process even after a timeout. Those
paths could lose ownership while an old child was still alive; a later maintenance
pass could then launch a replacement. Registry resync also reset the global pause,
allowing recording to resume despite the storage policy.

`recording/process_lifecycle.py` now confirms the directly owned child with a
bounded wait after both graceful and forced termination. Callers retain handles
when cleanup fails. Bulk shutdown still attempts every other child, then reports
`RecorderCleanupUnconfirmed` if any process failed to confirm exit. The watchdog
never forgets an unconfirmed child or books a completed failure prematurely.
Application shutdown uses `ExitStack` so a cleanup error still allows the other
services to stop; the error continues to propagate rather than reporting success.

A repeated pause retries pending cleanup. Global resume completes unfinished
pause cleanup before admitting replacement processes. `Recorder.start()` preserves
an existing global pause, including during DHCP/registry reconciliation.

The storage monitor separately remembers a pending pause application. A failed
callback is retried even if disk space recovers; failed resume retains storage
intent for the next check. Successful steady-state pause is not repeatedly applied.

## Internal per-camera gate

`Recorder.ingress` is a `RecordingIngressGate` in its own module. It shares the
recorder's admission lock; it does not own a background thread or a camera driver.

- `suspend(camera_id)` reserves the opaque public ID **before** terminating its
  recording child. Normal return confirms that child's retirement. Failure keeps
  both the gate and any remaining handle; retry uses the same bounded slot.
- `is_suspended(camera_id)` reports admission held, not confirmed termination.
- `resume(camera_id)` requires successful suspension and no remaining child
  handle. It only releases admission; normal maintenance starts the next consumer.
- Registry resync, global storage pause/resume and maintenance do not release this
  per-camera hold. Failed suspension cannot be bypassed by a later `poll()` result;
  an explicit retry must confirm it.

The gate is bounded to32 held IDs, matching the native source registry's maximum
capacity. Holds persist for the lifetime of this Recorder object, including camera
removal, until their owner explicitly releases them. They are not durable source
selection and are not exposed as a new dashboard/API action.

## Integration boundary

The future source coordinator must hold recorder admission while it gates viewers,
retires the old go2rtc producer and verifies physical closure. Before releasing a
hold for RTSP fallback it must separately confirm native-source cleanup. A reaped
recording FFmpeg only proves that **consumer** ended; go2rtc still owns the camera
connection and may have other consumers or reconnect work. This gate therefore
does not authorize activating the native source alongside the current RTSP feed.
Startup does not automatically suspend any camera.

## Offline evidence

Tests cover forced-kill receipts, retained ownership after watchdog/camera-removal
failure, partial batch cleanup, repeated pause, storage callback failures and
pause-preserving registry resync. Gate tests cover a selected camera while another
continues recording, failed suspension/retry, global resume isolation, bounded
capacity and a concurrent consumer startup serialized against suspension.

The focused recorder/storage run passed at103.8MiB with zero swap; mypy passed
319 backend source files at84.7MiB. No real camera commands or media encoders were
used for these new acceptance cases.
