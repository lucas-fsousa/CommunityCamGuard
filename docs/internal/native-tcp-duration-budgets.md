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

The initial staged target can be ten seconds of observation, but this is a proposed
acceptance step, not an enabled mode or promised success under today's cumulative
limits. Measure gaps and bounded buffer occupancy, confirm CLOSE/B9, then restore
diagnostics disabled. A shared-account lock currently covers the entire experiment;
longer ownership also delays controls on other cameras and needs explicit limits.
Minutes-long production streaming cannot simply reuse this exclusive diagnostic.

Production design still requires one driver-owned source per camera, bounded
fan-out/backpressure, a defined broker-session ownership model, RTSP fallback and
browser codec validation. Camera capabilities must remain unknown/disabled until
the driver has evidence for that model/device; this review enables none.
