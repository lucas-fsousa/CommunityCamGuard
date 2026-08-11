# Live-view observability

The MSE path can briefly stop when `SourceBuffer`, the browser decoder, or the upstream encoder falls
behind. It used to start immediately, retain five seconds of history and run at up to 1.25× to catch
up. That guaranteed an underrun because these streams emit fragments around their two-second GOPs.
The player now waits for a three-second startup cushion. It adapts between 0.85× and 1.05× when that
cushion shrinks or grows; an actual underrun seeks into the newest complete range, and a backlog over
eight seconds is discarded in one `live_edge_jump`. This avoids both the repeated loading spinner and
the conspicuous 2× replay of stale surveillance video. Buffer pruning uses 12/8-second hysteresis so
it does not add a remove operation for every incoming fragment.

An August 2026 reproduction also found a separate upstream failure: the preloaded HEVC→H.264 FFmpeg
could keep receiving packets while its output was not decodable. Consequently, packet progress is
not treated as proof of visible progress; a confirmed decoded-frame stall cycles the local HD
producer too. The browser is disposed first and the recovery waits two seconds before removing the
preload, otherwise the MSE relay can keep the old FFmpeg alive and PUT immediately pins the same
wedged process again. The base camera producer and recorder are not restarted.

That observation is useful but does not identify *why* the browser fell behind. The diagnostics below
correlate all three stages with UTC timestamps:

1. camera → go2rtc producer packet progress;
2. go2rtc/FFmpeg/container CPU, memory, PID state, restarts and OOM state;
3. browser transport, MSE queue/live gap, playback rate, decoded/dropped frames and WebRTC stats.

## Capture a reproduction

Run the host-side watcher in a second terminal while leaving the dashboard open:

```bash
.venv/bin/python scripts/watch_live_streams.py --verbose
```

It writes `tmp/live-diagnostics.jsonl`, rotates at 20 MiB and keeps two old files. The script is
host-side by design. `ccg-app` and `ccg-go2rtc` use separate PID namespaces; mounting the Docker
socket into the web app just so it can inspect FFmpeg would give a network-facing process
root-equivalent control of the host.

The browser sends no periodic beacon. It reports only state transitions (`waiting`, `stalled`,
`live_edge_jump`, `mse_failure`, `playing`, and watchdog recovery; `catchup_start/end` remain accepted
for cached older clients). The API adds go2rtc's packet counter/consumer count at that instant,
stores the last 200 events in memory, and writes one `live_view_event` JSON line to the app log. The
host watcher extracts those lines into the same JSONL sample as the process and stream metrics.

Recent browser events are also available to an authenticated session at:

```text
GET /api/media/client-events
```

## Reading the result

| Evidence at the same timestamp | Likely bottleneck |
| --- | --- |
| adaptive rate below 1×, low `bufferedGap`, decoded frames still increasing | Camera/transcoder is delivering in bursts; the player is preserving its live cushion |
| `live_edge_jump`, transport `mse`, growing `mseQueueBytes`/`bufferedGap`; server packet delta and FFmpeg CPU normal | Browser `SourceBuffer`, decoder, GC or rendering pressure; stale frames were discarded |
| repeated `waiting`/live-edge jumps plus high `ccg-go2rtc` or FFmpeg CPU | Host cannot transcode/push the live feed in real time |
| `video_packet_delta=0` for three samples while consumers remain attached | go2rtc producer/decoder stopped producing; local preload recovery is appropriate |
| packet delta healthy but WebRTC `framesDecoded` stops, packet loss/jitter rises | WebRTC transport or browser decoder |
| container restart count changes or `oom_killed=true` | Hard memory limit/OOM rather than a media timing issue |
| process state `D` or `Z` | Blocked I/O/kernel wait or unreaped process |

Commands and RTSP URLs are recorded only to distinguish recorder and live transcodes. RTSP userinfo is
redacted, and camera credentials/media payloads are never written.
