# Experimental inbound AV negotiation — 2026-09-14

`drivers/yoosee/p2p/av_receive.py` connects the existing bounded receive lifecycle
to complete-TLV correlation/decryption and one continuous V1 parser. It has **no
socket, INIT sender, production caller or dashboard capability grant**. The route
and cookie must come from an externally authenticated, fresh negotiation.

An exact 76-byte AV control with flags 0, matching call ID and ACCEPT action 2 is
required before media decryption. A complete V1 encoding header is then required
before activation. The same KCP and parser objects survive this transition;
partial records and early complete records are not discarded. Raw timestamps and
audio/video bytes are returned to the caller without playback, conversion or logs.

Wrong controls, malformed envelopes, unsupported flags, media before acceptance,
records before encoding, encoding changes and parser errors close both layers.
Closure releases the cookie reference and parser buffer; this is not a guarantee
of cryptographic memory erasure. Polling during silence is mandatory. Default
five-second initialization/progress and 30-second absolute limits are inherited.
No socket is owned: the future caller must close its transport on failure.

## Validation and deliberate rejection

Thirteen synthetic tests cover reordering and fragmentation of ACCEPT, split
encoding headers, a media record spanning activation, duplicate suppression,
wrong call/peer/conversation/cookie, header/flag/length errors, encoding changes,
initialization expiry and rejection of START as a substitute for ACCEPT.

The focused six-file receive/parser suite passed (73 tests), as did Ruff, Mypy
(172 source files) and the separate frontend camera-panel/PTZ contracts. The full
Python suite was attempted with a 512 MiB address-space/120 CPU-second cap but
terminated with exit 139 (native process crash); it is **not recorded as passed**.
The cause is not established. No larger-memory retry was made; the subsequent
focused suite exited cleanly and WSL still reported about 3.5 GiB available RAM.

A bounded replay attempt against the first packet of historical flow5 was
**rejected**, not validated: its first complete 76-byte control (sequence 0,
fragment 0, flags 0) is START/action 6, not ACCEPT/action 2. Existing targeted SDK
notes (`re/notes/onboard-recordings-playback.md`, ignored) distinguish the ACCEPT
handler from START and require action 2 for the initiating role. This experiment
therefore does not relax acceptance or infer negotiation roles from media alone.

A subsequent bounded scan of complete KCP control messages found the missing
distinction: on the same endpoint pair and base link, flows 4 and 5 are separate
inbound conversations. Flow4 contains ACCEPT (2); flow5 contains START (6).
The opposite direction contains INIT (1) in flow3 and START (6) in flow6. A second
route repeats this pattern in flows 7/8/9/10. This scan emitted only flow labels,
control flags/actions and counts, not endpoints, cookies or media. It is evidence
for a **two-conversation coordinator**, not evidence that one receiver should
accept START without the other channel's correlated ACCEPT. The new single-channel
composition remains a building block, not the final camera protocol owner.

The earlier offline decoder results remain valid: those inspect already captured,
cookie-correlated media and did not claim to reproduce the live initiator handshake.
The new receiver is proven synthetically only, not on a live camera or end-to-end
historical negotiation. No camera, container or production intercom was changed.

## Remaining work

Pin call IDs, direction bits and ordering across both historical conversations;
coordinate acceptance without merging their independent sequence spaces.
Implement the experimental outbound INIT/ACK
state and compose it with this receiver without sharing/reconstructing the
existing intercom's acknowledged sequence summaries. Then validate the complete
negotiation before a bounded camera-3 receive experiment. Keyframe deadlines,
pacing/A-V sync and single-producer production handoff remain separate work.
