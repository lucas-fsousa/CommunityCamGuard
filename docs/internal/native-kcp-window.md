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
timeouts. A separate synthetic full-buffer case also exposed that a completely
filled shared pending/partial budget cannot admit the missing head fragment;
that admission/window-accounting limitation is not relaxed or hidden here.
