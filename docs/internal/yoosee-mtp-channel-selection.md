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

## KCP route consumer and TCP envelope

Follow-up on 2026-10-10 identifies `iv_kcp_output` at `0x258e04` (428 bytes),
installed by `iv_create_kcb` at `0x258da4`. It reads a selected route slot from
session `+0x1b0` using index `+0x84c`, advances that index modulo 128, and calls
`iv_mtp_chnnel_send_mtp_frm` with control flag **zero** (`0x258f64–f68`). If no
selected route exists it searches for native channel 0x87, not any arbitrary TCP
candidate. Our experiment must explicitly own a measured TCP route; it must not
pretend that this UDP fallback established TCP readiness.

Consequently the same wrapper already traced for periodic meters produces
outbound **c0/60** for channel 0x86 (destination-ID prefix plus KCP bytes) and
**c0/00** for channel 0x85. Sending the existing canonical c0/10 datagram unchanged
would not reproduce this SDK TCP output. `iv_mtp_kcp_create` (`0x25df68`) uses
session `+0x5e8` for the base conversation and its high-bit counterpart for the
command conversation, matching the existing INIT versus START/CLOSE separation.

On input, `iv_on_mtp_tcp_frm` dispatches bit-7-clear records to
`iv_on_rcv_kcpdata_from_tcp` (`0x25b578`, 444 bytes). That function removes six
bytes in mode 0 and fourteen in nonzero mode, resolves the session by conversation
and selects the command/data KCP instance using the conversation high bit.

New socket-free `mtp_tcp_kcp.py` keeps this envelope adaptation separate from
the canonical parser and AV lifecycle. It validates original wire checksum and
length **before** constructing canonical c0/10 bytes, checks every coalesced
segment's exact expected conversation, rejects meter/outbound/unmapped input
envelopes, and caps complete TCP records at 1500 bytes, including the route prefix.
Inbound admission is deliberately limited to modes 0/2 (c0/10 and c0/50).
The c0/50 AV envelope is statically mapped and synthetically tested, **not yet
observed in a live AV exchange**. The eight-byte inbound prefix remains opaque;
conversation matching does not replace endpoint ownership or authentication.

Tests compose the adapter with `ReliableAvControl` for INIT/START/CLOSE, preserving
byte-identical retries and rejecting wrong-peer/stale-timestamp receipts and
receipts after closure. The adapter adds no socket, queue, retry loop or capability.
346 focused tests passed in 2.32 s, peak 86.4 MiB/no swap. No live AV was sent.

## Ownership and teardown follow-up

`iv_get_meter_item` (`0x257bfc`, 48 bytes) indexes a 16-item ring using sequence
low four bits. That lookup alone is not a stale-ACK guard; the diagnostic must
retain exact outstanding sequence/timestamp and connection ownership rather than
copying the SDK ring indexing as an admission policy.

`iv_mtp_session_free` (`0x25ade8`, 792 bytes) releases both KCP instances, clears
their pointers, frees buffers/timers and delegates channel cleanup.
`iv_mtp_chnnel_free` (`0x256d44`, 888 bytes) calls `ivtcp_close_socket` and
`ivtcp_close_notify` for its TCP resources, then frees channel storage. This is
local resource ownership, not evidence of AV CLOSE or broker B9 remote receipt.

## Bounded TCP record assembly

`mtp_tcp_framing.py` now assembles complete records independently of session/AV
logic. One framer belongs to one connection. It accepts only the mapped inbound
meter/KCP prefixes, validates original length/checksum, retains only an incomplete
suffix and never scans forward for a new magic byte after an error. Clean EOF,
truncated EOF, admission failure and cancellation all prevent instance reuse.
This prevents a reconnection from inheriting partial bytes; it is not camera
authentication or a substitute for per-record conversation validation.

The SDK-derived record limit is 1500 bytes. Additional local policies bound each
read at 4096 bytes and each returned batch at 64 records. A malformed complete
record discards the entire batch rather than returning its valid prefix. Callers
still need connection-wide byte/record/time budgets and must close their socket
on terminal errors. Empty `feed` is not EOF: the owner explicitly calls `finish`.

Every split point, one-byte reads, coalescing, partial tails, maximum records,
oversized declarations/reads, batch overflow, checksum failure and terminal reuse
are tested. Combined TCP and existing AV suites: 368 tests passed in 2.51 seconds,
87.2 MiB/no swap. No camera contacted, socket opened or production behavior changed.

## Next bounded work

1. Trace channel lookup and meter-ring sequence ownership so late or unrelated
   ACKs cannot revive a closed/currently replaced connection.
2. Compose one measured TCP connection with the now-tested record framer and AV
   lifecycle; the selected-route consumer and TCP envelope are mapped above.
3. Establish explicit socket/session teardown and one outstanding diagnostic
   request before a bounded camera-3 AV test. Broker B9 receipt remains unknown.

No further live test was performed for this static follow-up. Each disassembly
process had a 128 MiB/no-swap/50%-CPU/40-second cap, with measured peaks below
36 MiB. No production rebuild, other camera access or physical command occurred.
