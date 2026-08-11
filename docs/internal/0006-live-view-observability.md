# Live-view observability

The current player can briefly stop and then run at up to 1.25× until it reaches the live edge. This
specific behaviour is not the old permanent FFmpeg freeze: it is the MSE fallback accumulating data
while `SourceBuffer` or the browser decoder is temporarily behind. The explicit catch-up controller in
`frontend/video-rtc.js` raises `playbackRate` when the buffered live-edge gap exceeds one second.

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
`catchup_start/end`, `mse_failure`, `playing`, and watchdog recovery). The API adds go2rtc's packet
counter/consumer count at that instant, stores the last 200 events in memory, and writes one
`live_view_event` JSON line to the app log. The host watcher extracts those lines into the same JSONL
sample as the process and stream metrics.

Recent browser events are also available to an authenticated session at:

```text
GET /api/media/client-events
```

## Reading the result

| Evidence at the same timestamp | Likely bottleneck |
| --- | --- |
| `catchup_start`, transport `mse`, growing `mseQueueBytes`/`bufferedGap`; server packet delta and FFmpeg CPU normal | Browser `SourceBuffer`, decoder, GC or rendering pressure |
| `waiting`/catch-up plus high `ccg-go2rtc` or FFmpeg CPU | Host cannot transcode/push the live feed in real time |
| `video_packet_delta=0` for three samples while consumers remain attached | go2rtc producer/decoder stopped producing; local preload recovery is appropriate |
| packet delta healthy but WebRTC `framesDecoded` stops, packet loss/jitter rises | WebRTC transport or browser decoder |
| container restart count changes or `oom_killed=true` | Hard memory limit/OOM rather than a media timing issue |
| process state `D` or `Z` | Blocked I/O/kernel wait or unreaped process |

Commands and RTSP URLs are recorded only to distinguish recorder and live transcodes. RTSP userinfo is
redacted, and camera credentials/media payloads are never written.
