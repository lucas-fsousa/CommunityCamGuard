# Bounded native-media receive foundation — 2026-09-14

## Evidence and scope

The experimental `av_session.py` and existing `intercom_session.py` advance inbound
sequence with `max(previous, received + 1)` and interpret individual PUSH payloads.
That can acknowledge missing lower sequences cumulatively and is not a complete
fragmented-message receiver. This is a code-level finding, not a claim that it caused
the previous dashboard stalls (the live dashboard currently uses RTSP).

The reference [KCP implementation](https://github.com/skywind3000/kcp/blob/master/ikcp.c)
separates individual ACKs from cumulative `rcv_nxt`, advances the latter only through
contiguous input, and uses `frg` for message boundaries. Existing captured MTP/KCP
codecs provide the wire envelope; native message-mode use on every video channel
still needs capture verification. Do not assume KCP message boundaries equal video frames.

## New isolated modules

- `kcp_receive.py`: one pinned conversation, explicit initial sequence (zero for a
  newly established channel, never inferred from the first observed packet).
  Reorders out-of-order PUSHes, suppresses duplicates, handles unsigned-32 sequence
  wrap, and emits only complete descending-fragment messages.
- Default caps: 128 retained segments, 256 KiB retained payload, two-second assembly
  deadline. Config limits cannot exceed 128 segments/1 MiB/ten seconds. Joining a
  completed message briefly duplicates bounded payload memory; this is not an RSS cap.
- Overflow, conflicting pending duplicates, malformed chains or deadline expiry
  close/clear the receiver and raise sanitized errors. No silently dropping data
  already acknowledged and then continuing on that same reliable conversation.
- `media_receive.py`: rejects another peer, conversation, malformed checksum/length
  and oversized MTP datagrams; returns individual ACK bytes with contiguous UNA
  and available-window size, plus complete opaque messages. No sockets/threads,
  stored payload logs, camera writes, INIT, route discovery or reconnect loop.
- `build_kcp_ack` gains an optional validated window. Its default remains unchanged,
  preserving existing golden frames and the currently homologated intercom path.

Tests cover gaps, reverse arrival, duplicates, multiple messages, wrapping sequences,
wrong conversation/peer, byte/segment bounds, malformed fragment chains, expiry
without new input, corrupted datagrams and ACK fields. They use synthetic data,
no camera or SDK process. Production modules do not import the new receiver yet.

Validation: 39 focused receiver/wire-codec tests and the full Python suite passed;
Ruff, mypy (167 source files), and standalone Node camera-panel/PTZ contracts passed.
No browser, emulator, camera session, container rebuild or runtime switch was used.

## Next steps and explicit limits

1. Replay a bounded existing native-media capture to establish per-conversation
   initial sequence and message/stream mode. Do not seed from an arbitrary midstream
   packet or interpret an isolated fragment as an encoding header.
2. Feed complete messages into authenticated session-specific TLV decoding; establish
   encoding-header changes, video/audio frame boundaries, keyframes and timestamps.
3. Design bounded handoff from initialization to continuous reception, including
   preservation of pending acknowledged data. Merely passing an `inbound_next` scalar
   discards reorder/assembly state and is insufficient.
4. Only then integrate live input, heartbeat/window probes, finite lifecycle cleanup,
   codec synchronization and local fan-out. The owner must poll expiry during silence,
   send returned ACKs promptly, consume messages without an unbounded queue, and close
   the actual socket on ReceiveError. No automatic reconnect is implemented here.
5. Homologate on camera 3 without permanent parallel RTSP/native producers, measuring
   resource use, recording continuity and latency before any production preference.

This increment deliberately does not modify the working AV/intercom receive path:
safe migration must preserve state end-to-end and be separately homologated. The
MTP checksum and endpoint matching are not cryptographic media authentication.
