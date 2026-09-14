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

## Historical capture replay — 2026-09-14

Implemented `scripts/pcap_input.py` (streaming classic PCAP/RAW IPv4 reader) and
`scripts/replay_native_media.py` (sanitized receive-only replay). Run from the repo:

```sh
prlimit --as=268435456 --cpu=30 -- .venv/bin/python -m scripts.replay_native_media re/pcapdroid/pcap.pcap
```

No network, persistent output, payload/credential dumps or address logging. The
report labels conversations `flow1` etc. It keeps up to 16 directional conversations,
reads at most 64 MiB/100,000 records and rejects oversized packet declarations before
allocation. Only classic RAW-IPv4 PCAP is supported, not Ethernet/PCAPNG/IP fragments.
The capture remains git-ignored; no private packet fixture was committed.

Existing 4,443,195-byte capture:

| Observation | Result |
|---|---|
| Input records | 12,436 |
| Valid MTP datagrams / invalid MTP datagrams | 7,222 / 0 |
| Directional PUSH conversations | 10, all containing sequence zero |
| Conversations without receiver failure | 9; no retained tail bytes |
| Largest successful conversation | 2,838 messages, 1,530,267 bytes; 11 buffered observations |
| Duplicate filtering example | 27 PUSH observations → 25 messages |
| Nonzero KCP fragment counters | 0 (fragment assembly remains synthetic-test evidence) |
| Emitted message TLV lengths | All matched the declared 16-bit length |
| Terminal failure | flow9: missing sequence 236, two-second assembly deadline |
| Largest observed buffered payload before failure | 119,161 bytes, subsequently cleared |
| Offline process measurement | 0.67 s, peak RSS 51,280 KiB; 256 MiB address-space / 30 CPU-s limits |

The blocked sequence in flow9 does not appear later in that captured direction.
This proves a gap **in the capture**, not whether the cause was network loss,
capture loss, sender behavior, or an ACK problem in the vendor app. No deadline or
memory limit was raised to hide it; the receiver did not skip to an arbitrary later
sequence. The capture contains traffic for older devices, not a fresh camera-3
homologation. Playback/codec correctness and live recovery remain unproven.

This completes the initial transport replay step for the observed unfragmented
TLV traffic. Next is session-cookie correlation and complete-message decryption,
followed by bounded StreamPipe/frame parsing. TLV messages are not assumed to be
individual video frames. Current recording/intercom paths remain untouched.

Twelve new synthetic-PCAP tests cover byte order/microsecond/nanosecond clocks,
reordered records, missing start, terminal-gap reporting, malformed/truncated lengths,
IP-fragment rejection and sanitized output. They need no external capture files.

## Cookie-correlated offline decoding — 2026-09-14

`scripts/captured_media.py` extends the same bounded replay, without production
imports or runtime changes. It accepts exact 177-byte direct mode-1 A4 envelopes
only after declared length, mode, checksum, option layout and 24-bit link validation.
Bindings include both complete UDP endpoints and the link ID; the conversation's
high direction bit is masked only for that lookup. Conflicting keys for the same
binding remain ambiguous, never last-writer-wins. Up to 16 bindings are retained.

Only a complete 76-byte type-3 AV message with matching call ID establishes the
per-flow cookie association. Type-4 complete TLVs with captured flags 1/2 can then
use the existing RC5/6 cookie decoder. Missing/mismatched/changed associations skip
decryption. Cookies and call IDs have no diagnostic repr and are absent from reports.
This is historical protocol correlation, **not cryptographic authentication**.

Inspection retains only the first 28 decoded bytes, even across multiple TLVs, for
the existing V1 encoding-header parser. It never searches arbitrary plaintext for
a plausible header, saves decoded media, logs audio/video, or assumes a TLV is a
video frame. Consistent codec metadata is a useful decoding check, not a playback test.

Captured results:

| Flow | Decoded complete media TLVs | Decoded bytes | Encoding header |
|---|---|---|---|
| flow5 | 2,837 | 1,518,843 | 640×360, 15 fps |
| flow9 | 235 before the same missing-sequence deadline | 231,650 | 1920×1080, 15 fps |
| flow2 | 0 (24 skipped: no correlated key) | 0 | none inferred |

Both headers report video codec ID 5 and audio codec ID 4, option 2, mono, 16-bit,
16 kHz, 1,024-sample frames. These are recorded header fields, not an independent
decoder validation or a guarantee of observed playback rate/resolution. No keys
from another session were tried on flow2, and flow9 still stops at the original gap.

Ten new synthetic tests cover endpoint/link/checksum binding, ambiguous-key refusal,
call-ID mismatch, missing controls, changed key, unsupported flags, wrong-cookie
header failure and a header split across complete TLVs. Next: interpret bounded
StreamPipe records after the header, establish complete frame boundaries/timestamps,
then validate codec access units. No live test or container rebuild in this increment.

Validation: full Python suite and focused replay/correlation tests passed, as did
Ruff and mypy for the offline modules. Full historical decoding measured 1.59 s
and peak RSS 50,972 KiB under 256 MiB address-space / 30 CPU-s limits.
