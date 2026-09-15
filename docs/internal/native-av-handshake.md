# Experimental AV handshake ownership — 2026-09-15

`p2p/av_handshake.py` composes `ReliableAvControl` and paired `AvReceiver` into one
socket-free owner. It is not registered in the production driver, does not open a
route, send a datagram, start a decoder or change the current RTSP/intercom path.

## Independent conditions

1. Emit INIT on the control conversation. Retries keep the original sequence/wire.
2. Require its exact transport ACK plus the matching peer ACCEPT and peer START.
3. Only then emit local AV START on the independent base conversation.
4. Report negotiation readiness only after local START's exact ACK and a parsed
   encoding header, while all the preceding conditions still hold.

`ready` means negotiation/header readiness, **not a decoded keyframe or playable
live source**. The future media owner must separately enforce keyframe deadlines,
codec validation and source handoff. Early encoding/media records are returned
immediately, even when `ready` is false; callers must consume them continuously
without presenting a ready live source prematurely. No extra frame queue is owned.

The API is `due()` for bounded outgoing controls, `receive()` for incoming datagrams
and returned ACKs/records, `poll()` during silence and `close()` on cancellation or
socket-send failure. Returned outgoing wires count as attempted transmissions. The
same parsers, pending fragments and senders persist throughout negotiation; ACKed
state is never reconstructed from a scalar sequence number.

INIT's receipt is not inferred from ACCEPT. START is not inferred from a transport
ACK. Repeated/unsolicited/wrong-peer input cannot start a new negotiation or duplicate
records. Receive or send expiry closes both layers and releases buffered bytes.

## Bounds and validation

An absolute five-second readiness deadline supplements each sender's two-second
receipt limit. Existing receive byte/fragment/progress limits and the thirty-second
experimental lifetime remain. No background work, sockets or unbounded queue was
added. The owner does not handle discovery, session credential renewal or retry a
failed negotiation on the same reliable channel.

Nine synthetic tests cover the integrated sequence, header and partial media before
a delayed START ACK, exact duplicate suppression, ACK-versus-ACCEPT separation,
byte-identical INIT retry after lost receipt, missing START receipt despite media,
missing encoding despite receipts, malformed protocol, unsolicited input, wrong
peer, and terminal cancellation/cleanup. Tests do not act on a real camera.

START arriving before ACCEPT across the independent channels now uses the bounded
ciphertext queue described in [native-av-reorder.md](native-av-reorder.md). The historical paired replay
and earlier codec validations remain separate evidence, not proof of this owner's
live handshake or physical camera interoperability. The bounded socket adapter is
now simulated in [native-av-probe.md](native-av-probe.md); remote teardown and the
camera-3 live experiment remain pending before any source switch.

Regression verification: 126 selected handshake/send/receive/V1/existing media-session
tests passed under a 512 MiB address-space cap. Ruff, Mypy (178 source files) and the
frontend toast/panel/PTZ contracts passed. No full-suite retry, build or service restart
was performed for this isolated experimental addition.
