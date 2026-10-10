# Native MTP ACK and channel selection

Static SDK 6.45 ARM64 evidence, 2026-10-10. This follows the
[camera-3 TCP roundtrip](yoosee-push-live-observation.md). It is not an enabled
runtime transport, a media acceptance result or a recommendation to duplicate
the vendor's entire channel scheduler.

## ACK accounting

`iv_rcv_meter_ack` (`0x25db20`, 1096 bytes):

- Resolves body offset 6/14 from prefix bits 5–6, then looks up the existing
  channel by address and transport mode through `iv_findMtpChnByAddrAndType`
  (`0x25db94`). A checksum-valid packet alone does not create a channel.
- Looks up the sequence's meter item (`0x25dbe8`), sets item status 2 and stores
  monotonic-now minus the echoed full timestamp as a 16-bit RTT (`0x25dbfc–dc10`).
- For session role 1, calls `iv_mtpSession_optimize_proc` (`0x25dc2c`).
- Records the received record length and resets channel `+0x12c` to zero
  (`0x25dd74–dd80`, `0x25df50–df54`). The UDP MTU branch is specific to channel
  0x87 and sequences 7/8; it must not be borrowed for the TCP relay codec.

The observed response matches the inputs to this accounting, but our diagnostic
does not instantiate or claim to execute the SDK's in-memory channel machinery.

## Quality and route selection

`iv_mtp_chnnel_eval_quality` (`0x2575a0`, 1628 bytes) scans a 16-entry meter ring
at channel `+0x3c`, counting status 2 as successful samples and status 3 separately
as failed samples. It calculates mean RTT and failed-sample percentage. A stale
channel can have its score cleared when `+0x12c > 8` and the session time field
at `+0x8a4` is more than 10 seconds old. The precise writer/lifecycle of these
fields still needs tracing; this is not a proposed application timeout.

The score calculation distinguishes relay channels 0x86/0x88, 0x85 and the other
channel types, combining meter loss, RTT and a KCP-related metric. It initializes
a zero score directly, while an existing score normally mixes 70% old with 30%
new (except session role 3). Do not translate a single ACK into a universal
quality threshold or successful AV authentication.

`iv_mtpSession_optimize_proc` (`0x2597e8`, 3472 bytes) evaluates every known channel
and sorts them. Candidate grouping examines score thresholds 70, 60, 50, 40, 30,
then 1. Separate direct-channel preferences may override that grouping. If only
one candidate remains, all 128 route slots at session `+0x1b0` receive its pointer
(`0x25a25c–a2c0`). Otherwise special preferences or weighted distribution over at
most two candidates fill those slots. This is route selection, not AV startup:
neither the ACK handler nor the optimizer calls an AV INIT/START builder.

## Next bounded work

1. Trace channel lookup and meter-ring sequence ownership so late or unrelated
   ACKs cannot revive a closed/currently replaced connection.
2. Trace the consumer of the selected route slots and the AV control envelope
   for TCP relay. Reuse existing AV codecs only after confirming that envelope.
3. Establish explicit socket/session teardown and one outstanding diagnostic
   request before a bounded camera-3 AV test. Broker B9 receipt remains unknown.

No further live test was performed for this static follow-up. Each disassembly
process had a 128 MiB/no-swap/50%-CPU/40-second cap, with measured peaks below
36 MiB. No production rebuild, other camera access or physical command occurred.
