# Bounded AV control transmission — 2026-09-15

`p2p/av_control_send.py` implements a socket-free sender for **one** AV INIT,
START or CLOSE control. INIT/START require a freshly negotiated conversation;
CLOSE requires the exclusive owner to have completed START. It is not PTZ, microphone
START, an arbitrary camera-command retry service or a complete KCP implementation.
Production AV initialization/intercom do not call it.

INIT uses the link's high-bit conversation; AV START uses the base conversation.
Both begin at sequence zero in their independent outbound spaces. CLOSE uses the
base conversation at sequence one, as detailed in [native-av-close.md](native-av-close.md).
The codec builds
the existing control bytes; at most four attempts are emitted 250 ms apart within
a two-second absolute deadline. Retransmissions are byte-identical, including
sequence and timestamp: they are not new application-level INIT requests. Silence
must be polled by the future socket owner. A due packet counts as attempted even
if sending fails; the owner must close the sender/session on that failure.

Only an ACK for an issued packet, from the pinned endpoint, matching conversation,
sequence and echoed 32-bit timestamp completes the transaction. ACK bodies and
nonzero fragment counters are rejected. Cumulative UNA is not used to infer an
ACK for another request. Duplicate ACKs, unrelated traffic and malformed/oversized
datagrams do not extend deadlines. Deadline expiry and cancellation are terminal;
completed/closed senders release the retained wire payload.

This retains a single 106-byte request and scalar state, with no queue, worker,
socket, adaptive RTO, window probing, fast retransmit or general media sender.
Fixed retry limits are experimental, not claimed to reproduce every SDK policy.

## Evidence

A bounded offline scan of the historical PCAP found 14 unique complete AV control
transmissions and 12 matching reverse-direction ACKs using the exact endpoint,
conversation, sequence and timestamp tuple. Two requests had no observed exact
receipt; this does not prove that the camera never received them. The scan emitted
counts only. It validates the correlation fields, not live behavior of this sender.

Twenty-one synthetic tests cover INIT/START channel separation, exact receipts,
byte-identical bounded retransmissions, silence expiry, cancellation, unsent/stale/
wrong-peer/wrong-channel/sequence/timestamp ACKs, malformed frames, unsupported
actions, timestamp wrap and coalesced ACK segments. No packet was sent to a camera.

## Remaining integration

Update: the socket-free composition is now implemented and synthetically tested in
[native-av-handshake.md](native-av-handshake.md). The bounded socket probe and CLOSE
are simulated in [native-av-probe.md](native-av-probe.md) and
[native-av-close.md](native-av-close.md); live route integration remains pending.
This does not change production intercom.

The future owner must coordinate this sender with `AvReceiver`, gate outbound
START on correlated ACCEPT (not merely INIT's transport ACK), preserve independent
send/receive sequence spaces, and handle bounded cross-channel reordering. It must
not reconstruct sequence zero after a partially acknowledged session or reuse this
object for subsequent controls. Keep ACK receipt, application acceptance and media
readiness as separate conditions. A live camera-3-only handshake and single-producer
source switch remain unimplemented; RTSP continues unchanged.

Verification: 115 selected send/receive/V1/existing-media-session tests passed before
the final two sender cases were added; the final 21-case sender suite also passed.
Ruff, Mypy (177 source files) and frontend toast/panel/PTZ contracts passed. All Python
checks used a 512 MiB address-space cap. No rebuild, service restart, camera session
or larger-memory/full-suite retry was performed.
