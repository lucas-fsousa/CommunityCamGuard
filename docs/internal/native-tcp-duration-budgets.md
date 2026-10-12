# Native TCP duration budget review — 2026-10-11

The camera-3 short in-server run is validated, including positive broker receipt.
It is not a sustained-stream implementation. Extending only the outer receive
timeout would collide with independent limits below; none were increased here.

| Layer | Current bound | Consequence for a longer experiment |
|---|---|---|
| `BudgetSocket` | 20 s initial phase; 1.5 s separate cleanup phase | Authentication/discovery/service share initial time; reserve cleanup explicitly |
| `BrokerMaintenance` | 15 s, 512 datagrams, 1 MiB cumulative, two fragment groups | Media loop must not outlive broker servicing; no heartbeat/renewal inferred |
| `receive_paired_av` | 8 s total; observe 3 s after ready; CLOSE at most 2 s within total | Observation is not the whole route duration |
| `TcpAvSession` | 3 s measurement; 15 s total; 2 MiB RX, 256 KiB TX, 4,096 records | Cumulative traffic is a finite experiment budget, not a retained-memory measure |
| `AvHandshake` | 5 s readiness | Increasing sustained duration must not relax startup validation |
| `ReceiveLifecycle` | 5 s initialization/idle, 30 s lifetime | ACKs/duplicates do not count as complete-message progress |
| `KcpReceiver` | 128 segments, 256 KiB default buffered, 2 s assembly | Preserve per-message storage and partial-message deadlines |
| `PendingAv` | 128 messages, 256 KiB | Pre-accept buffering must remain bounded and drained |
| `MtpTcpFramer` | 4,096-byte input, 64 records per feed | Keep incremental parsing; do not accumulate the whole stream |
| `AvVideoSample` | 120 video frames, 2 MiB | Optional finite decoder sample, not a sustained recording sink |

Next implementation should use a reviewed, immutable experiment budget propagated
through the owners, not unrelated environment knobs or browser-supplied numbers.
Keep startup, idle, fragment and buffer bounds independent of total duration.
Test invalid/inconsistent budgets before allocating sockets, and exercise silence,
continuous ACK-only traffic, backpressure and cancellation with a fake clock first.
An extended test should count/discard completed media immediately; keep any decode
sample independently capped. Do not remove traffic limits or add an unbounded queue.

The initial staged target is ten seconds of observation. Measure gaps and bounded
buffer occupancy, confirm CLOSE/B9, then restore
diagnostics disabled. A shared-account lock currently covers the entire experiment;
longer ownership also delays controls on other cameras and needs explicit limits.
Minutes-long production streaming cannot simply reuse this exclusive diagnostic.

Production design still requires one driver-owned source per camera, bounded
fan-out/backpressure, a defined broker-session ownership model, RTSP fallback and
browser codec validation. Camera capabilities must remain unknown/disabled until
the driver has evidence for that model/device; this review enables none.

## Implemented finite extended profile — offline validation

`TcpProfile` is an enum of immutable reviewed budgets, not arbitrary numeric input.
SHORT preserves the original limits above. EXTENDED observes ten seconds with
20 seconds for TCP reception including CLOSE, 22 seconds for its protocol owner,
28 seconds for broker servicing and 48 seconds for the whole route before the
independent 1.5-second cleanup phase. Authentication still receives its original
20-second deadline, measurement three seconds, readiness/idle five seconds and
assembly two seconds. Broker packet/byte and all retained-buffer limits stay fixed.
TCP cumulative traffic is capped at 8 MiB RX, 1 MiB TX and 16,384 records; these
are counters, not buffers. No indefinite session, retry or renewal is added.

EXTENDED rejects a decoder sample before socket allocation; the receiver also
rejects and clears one if invoked directly. Completed media is counted/discarded.
The guarded server option `NATIVE_AV_DIAGNOSTIC_TCP_PROFILE=extended` requires
TCP enabled and decode/platform-only disabled. Default remains `short`; no HTTP
parameters can select a profile. All original authentication, camera/broker locks,
reviewed server target and one-shot gates remain in force.

Offline tests exercise ten seconds of continuous real-protocol messages with a
fake clock, CLOSE, drained buffers, unchanged startup/idle failures, immutable
profiles, rejection before sockets, broker limits, cancellation and profile
propagation through both route owners. This is not yet a new live result.

## Ten-second in-server live result — 2026-10-11

After CI 38171577350 passed for `c9aba40`, built image `f8d6a35d51c7` with
512 MiB/no-extra-swap/one-CPU build limits and recreated only the app. One camera-3
EXTENDED attempt, with no decoder sample, returned HTTP 200:

| Observation | Result |
|---|---:|
| Video records delivered | 160 |
| Audio records counted, not played | 164 |
| Encoding headers | 1 |
| TCP received / sent bytes | 1,266,503 / 53,992 |
| Ready / first video from receiver start | 1,259 / 1,442 ms |
| Maximum video interarrival gap | 312 ms |
| Trailing silence before CLOSE | 0 ms (rounded) |
| Observation duration including startup | 11,264 ms |
| AV CLOSE / correlated B9 receipt | both true |
| Broker messages serviced | 5 |

The observed ready-to-CLOSE window is 10,005 ms. These are local arrival timings,
not camera capture latency, browser playback measurements or a strict frame-rate
claim. The same HD startup profile was requested, but this counts-only attempt
does **not** constitute another decoded-resolution validation. The previous
55-frame strict decode remains the resolution evidence.

Finally restored four flags false, both target IDs empty and profile `short`;
health returned 200. All three current-hour recorder directories grew over ten
seconds: 786,432, 1,500,980 and 1,875,831 bytes. Two app recreations briefly
interrupted recording. go2rtc and unrelated containers were not restarted.
Client cgroup peak was 86.5 MiB without swap (not total server memory); afterward
app used 201.8 MiB and go2rtc 413.1 MiB, each retaining its 1 GiB ceiling.

Ten seconds does not homologate minutes-long streaming or the dashboard. Next
engineering boundary is source/session ownership: a production source cannot
hold the driver's shared control lock indefinitely or be invalidated by another
control login. Resolve that contract before wiring a generic single producer and
bounded consumers. Do not promote this diagnostic, repeat it automatically or
enable a native-stream capability on any model from this evidence alone.
