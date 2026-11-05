# Native KCP receive-window queries

SDK6.45 ARM64 `libiotvideomulti.so`: `ivkcp_input` at0x2676b4 recognizes
PUSH0x51, ACK0x52, WASK0x53 and WINS0x54. At0x267d34 the WASK branch sets
the pending window-report bit (`probe |= 2` at0x267d58); `ivkcp_flush`
at0x2689b8 emits command0x54. This is separate from AV ACCEPT/START.

Our receive-only adapter previously ignored WASK. It now returns one bounded,
checksummed WINS through the existing owner/socket and TCP envelope, reporting
current receive credit and next unacknowledged sequence. It neither acknowledges
a PUSH nor advances sequence state, allocates payload history, renews timeouts,
creates a connection, or grants authentication/readiness.

Wrong peer/conversation, checksum failure, nonempty/fragmented queries, incoming
WINS and unknown commands produce no report. Existing framing/rate/memory limits
still apply. Tests cover current credit after gap closure, zero/nonzero reports,
unchanged missing-fragment deadline, and both AV conversations before/after
acceptance through the actual TCP session adapter.

This fixes a protocol omission, not the proven cause of the observed gap1
timeouts.

## Admission and startup credit correction

The synthetic full-buffer case exposed another defect: future segments could
fill the shared pending/partial segment budget, leaving no slot for the missing
head. The old receiver then failed even when that head finally arrived. Reserve
one existing slot for the head: a future segment that would occupy it is not
retained and not ACKed, so it must be retransmitted after the gap closes. No
already-ACKed segment is evicted, no window/byte limit is enlarged and duplicates
still receive their existing ACK. The default128-segment/256KiB budgets can hold
128 maximum-size validated payloads within their original byte bound.

INIT/START/CLOSE also inherited the generic PUSH encoder's window256, despite
the actual owned receivers having128 slots. Native reliable controls now explicitly
advertise128 from the receiver's default constant. Other PUSH encoder users keep
their original default; the optional explicit window is range-checked.

Tests exercise partial message + future queue pressure, refusal without ACK,
successful missing-head assembly, later retransmission and all three opening/
closing controls' advertised credit. Timeout facts add only three numeric counters
(number of future rejections, first/last rejected sequence) to distinguish local
window rejection from a segment never reaching the receiver. These are constant
space, range-checked and contain no payloads. Assembly deadlines stay unchanged.
