# RTSP talkback timing diagnostics

## Split refresh/write measurements

Following physical acceptance of the recorded message and reported gaps in the
paced stream, the summary now separates mid-audio watchdog refresh from media
socket writes. `refresh_calls`, `refresh_failures`, `max_refresh_ms` measure only
periodic OPEN requests, excluding initial OPEN/CLOSE. `write_calls`,
`write_failures`, `max_write_ms` measure interleaved RTP writes, excluding refresh
and packet packing. Both include failed-call duration via finally, without logging
exception text. Existing `max_send_ms` retains its original successful combined
call meaning; maxima may come from different packets and must not be subtracted.

Synthetic tests inject240ms refresh and7ms write delays independently, including
failure of either operation, and verify cleanup. This is constant-memory aggregate
instrumentation, not a pacing/codec/prebuffer change. No extra socket/thread or
camera request is introduced. It does not measure upstream waits or camera buffer
occupancy. Requires deployment and one subsequent authorized audio test to identify
the dominant real delay; the current gaps are NOT fixed or revalidated yet.

The Yoosee driver logs one `rtsp_audio_timing` summary after each `_send_chunks`
operation, including partial failure and cleanup. It stores only aggregate counters
in memory. No PCM, credentials, native device ID, IP, URL, exception text or per-packet
samples are logged. Only a validated opaque public camera ID is included.

- `packets`: successful local socket writes, not camera acknowledgements.
- `late_packets`: writes completed more than10ms after their nominal playback time.
- `max_lateness_ms`: maximum positive completion delay relative to that timeline.
- `max_send_ms`: maximum successful send-call duration, including OPEN refresh when
  it occurs inside the call. Failed calls are not included in these timing maxima.
- `elapsed_ms`: session duration including opening and cleanup.
- `opened` / `released`: decoder-open and cleanup results; these alone do not prove
  successful complete audio delivery.

The nominal timeline starts with the first preloaded packet and advances10ms per
native packet. The existing100ms prebuffer is unchanged. Waiting on upstream audio
or scheduling can exhaust that lead; synchronous socket/refresh stalls also affect
completion. No automatic timing adjustment or replay is introduced. These counters
help locate server-side delays but cannot measure actual camera-buffer occupancy,
speaker underflow, full-duplex behaviour or physical audibility.

Inspect locally using `docker compose logs --since 10m app` and filter for
`rtsp_audio_timing`. These summaries use existing container log rotation; unlike
orientation JSONL diagnostics, they do not survive container recreation. Do not
publish unrelated historical logs without inspecting them for sensitive content.

Validation2026-10-19:23 focused timing/backchannel/adapter tests passed89.3MiB/no
swap, plus Ruff. Synthetic tests cover buffering versus missed deadlines, identity
sanitization, a successful ten-second session and failed watchdog refresh, each with
one summary and preserved CLOSE behaviour. No camera I/O or sound. Source change
requires the next app rebuild; physical timing correlation remains pending.

## Deployment, 2026-10-20

Image59ca813fb59c includes the instrumentation and explicit INFO logger configuration.
Source3f43324 passed CI39333736881 before deployment. Only app was recreated at
15:32:23UTC; go2rtc retained its October19 13:03:15UTC start. Both services reported
no OOM and zero restart counters. The installed logging/timing source hashes match
the workspace. A separate process inside the container checked the installed logging
configuration: INFO enabled, one handler, no propagation. This is a configuration
check, not an injected event or introspection of the running server's logger.

Health/authenticated status/filesystem check passed: all three cameras online and
recording, newest nonemptyMP4 files2s old (56MiB/no swap). Camera3's current catalogue
preserves both audio modes and all seven static controls (43.4MiB/no swap for audio
catalogue verification). No real audio, media request or camera action was sent.
Physical timing correlation is still pending; the rebuild prerequisite is complete.

Deployment preparation found that the bundled Uvicorn configuration did not enable
INFO for this module. Added an explicit handler/INFO/no-propagation entry only for
audio_timing; generic driver INFO/debug remains disabled. A fresh-process test using
the actual logging configuration emits exactly one synthetic summary and suppresses
an unrelated driver INFO message.18 focused tests pass126.1MiB/no swap. No camera I/O.
