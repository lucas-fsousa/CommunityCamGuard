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

## Incremental V1 record boundaries and raw timestamps — 2026-09-14

Added `p2p/v1_receive.py`, used only by offline `scripts/captured_frames.py`.
The production stream/intercom paths remain unchanged. The parser consumes decoded
bytes incrementally across TLVs and emits complete records, never partial frames
or a guess found by scanning payload for the magic value.

The historical RE `re/mtp_stream.py::build_v1_audio_packet` documented the AV layout.
This increment independently inspected just `trans_proto_v1::unpacking_avdata`
at `0x144a74` (1,488 bytes) in the existing 2,363,696-byte
`re/extracted/libiotvideomulti.so`, using the symbol-aware disassembler with
256 MiB address-space / 20 CPU-s limits. It confirms:

| Offset | Meaning |
|---|---|
| 0 | V1 magic, four bytes |
| 4 | Record marker; this parser permits observed AV values 0/8 and encoding headers |
| 6 | u16 audio frame count |
| 8 | u32 trailing video payload size |
| 12 | u64 video timestamp |
| 20 | u64 shared audio-record timestamp |
| 28 | u16 audio-size descriptors, then audio bodies, then video body |

Native instructions at `0x144b24` read the audio count from header+6;
`0x144cd0` adds video length from header+8 to summed audio sizes before committing;
`0x144d70` loads the audio timestamp from header+20 and `0x144e68` loads video time
from header+12. Audio payloads are consumed before the trailing video payload.
No broad APK decompilation, SDK execution or camera connection was needed.

Bounds: 65,535-byte feed chunks, 256 audio descriptors, 1 MiB declared record size;
retained bytes never exceed one record plus one input chunk. A completed record is
copied into bounded output; callers must consume/release it, not accumulate frames.
Unsupported record classes or invalid lengths close/clear the parser. No implicit
resynchronization, codec decoding, timestamp conversion or format-wide capability
grant. The live owner will still need a no-progress timeout for an incomplete record.

Historical replay now yielded:

| Flow | Encoding headers | Audio frames / bytes | Video payloads / bytes | Tail bytes |
|---|---|---|---|---|
| flow5 (640×360) | 1 | 1,172 / 308,200 | 1,137 / 1,152,719 | 0 |
| flow9 (1920×1080, before KCP gap) | 1 | 3 / 806 | 3 / 230,670 | 0 |

Neither stream produced a record-boundary error or raw timestamp regression.
Flow5 consecutive audio-record deltas were 59,000–69,000 ticks; video deltas were
49,000–150,000 ticks. Flow9 audio deltas were exactly 64,000; video 50,000–100,000.
Microseconds are consistent with the recovered header (1,024 samples / 16 kHz =
64 ms) and existing sender contract, but this parser preserves raw u64 values.
It does not interpret them as UTC, infer a date, or claim a constant observed FPS.
Grouped audio frames share a record timestamp; per-frame pacing is separate work.

Seventeen new tests cover byte-by-byte/chunked input, multiple records, headers,
partial audio tables/video bodies, magic inside payload, unknown records, oversized
lengths, closed receivers, relative timestamp diagnostics and incomplete capture tails.
Next: validate elementary audio/video payloads with a bounded independent decoder,
establish codec configuration/keyframe transitions and only then design live fan-out.

Validation: 39 focused framing/correlation/replay tests passed; Ruff and mypy passed.
The full historical replay retained peak RSS 51,724 KiB under the existing 256 MiB
address-space cap. This run took 20.16 s wall time while the host was also busy;
it is not a production decoding performance benchmark. No browser, native SDK
execution, container rebuild, new media session or camera action occurred.

## Independent elementary decoder validation — 2026-09-14

Added `scripts/validate_native_decode.py`: an explicit flow/kind callback collects
up to 8 MiB of elementary payload in memory from complete, cookie-correlated V1
records. Requires the initial encoding header and rejects configuration changes.
No media files, camera connections or audio playback. Missing/unmapped codecs and
empty samples fail before spawning a decoder. The default replay remains content-free.

For each selected sample, FFprobe counts decoded frames and reports whitelisted
codec/geometry/audio metadata, then FFmpeg runs strict `-xerror -err_detect explode`
decoding to the null sink. Processes run sequentially, decoder/filter threading is
limited to one, pipe is the only enabled input protocol, and each process has
512 MiB address-space, 30 CPU-s, 64-FD and 45-second wall limits. No parallel decoder
jobs were launched. The sample memory budget is separate from decoder memory.

Reproduce one check at a time:

```sh
prlimit --as=536870912 --cpu=60 -- .venv/bin/python -m scripts.validate_native_decode re/pcapdroid/pcap.pcap --flow flow5 --kind video
```

Change `--kind` to `audio`, or select `flow9` to validate only its recovered prefix.
Output contains counts/metadata/errors, never raw decoded bytes, keys or addresses.
Strict-decoder, header or frame-count mismatch produces nonzero CLI status.

| Historical sample | Input / independently decoded frames | Independent codec/format | Strict decode |
|---|---|---|---|
| flow5 video | 1,137 / 1,137 | HEVC, 640×360 | passed |
| flow5 audio | 1,172 / 1,172 | AAC, mono, 16 kHz | passed |
| flow9 video prefix | 3 / 3 | HEVC, 1920×1080 | passed |
| flow9 audio prefix | 3 / 3 | AAC, mono, 16 kHz | passed |

Formats match their V1 encoding headers. Both flow9 reports still explicitly carry
`KCP assembly deadline exceeded`; successful prefix decoding is not successful
whole-flow recovery. There were no V1 record errors or incomplete V1 tail bytes.
Raw elementary pipes do not preserve the proprietary timestamps: this test proves
codec payload validity, not playback timing, A/V sync or live end-to-end latency.
It is historical evidence only, not native live-stream homologation on camera 3.

Ten socket-free/subprocess-mocked regression tests cover sample limits, isolation,
missing/changing headers, explicit resource/protocol limits, timeout/probe failure,
metadata sanitization and decoder/format/count mismatches. Next: configuration and
keyframe transitions, reconnect at decodable boundaries, then a bounded native
receive lifecycle and single-source handoff. Production RTSP/intercom are unchanged.

The full Python suite, 49 focused offline tests, Ruff, mypy for the four touched
analysis modules, and standalone Node panel/PTZ contracts passed. No container
rebuild or live camera test was performed for this increment.

The next offline milestone is now complete: [native-video-recovery.md](native-video-recovery.md)
documents configured-IDR gating, three independently decoded restart scenarios,
and a negative control demonstrating failure when restarting on dependent pictures.
Live transport reconnect/source handoff remains separate, unimplemented work.
