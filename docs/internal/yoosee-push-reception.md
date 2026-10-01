# Push TCP reception and callback ownership

2026-10-01, SDK 6.45 binary identified in [platform provenance](yoosee-platform-sdk-versions.md).
Continues [certification](yoosee-push-certification.md). Offline only: no transport
enabled, camera contacted, credential loaded or runtime media process changed.

## Callback registration and transitions

`iv_push_rly_tcp_conect` (`0x279324`, 744 bytes) passes the push session as the
callback context to `ivtcp_comm_add_connect`. For terminal mode other than 2,
node `+0x38` owns the resulting socket, using address `+8`. Its connect callback
comes from GOT `0x2a86f8` = `iv_on_push_tcp_connect_finished`. Socket `+0x78` gets
the close callback from GOT `0x2a8700` = `iv_on_push_tcp_closed`.

`iv_on_push_tcp_connect_finished` (`0x27960c`, 576 bytes) reads socket context
`+0x90` and resolves the relay node. Socket status 2 clears node `+0x38` and sets
session `+0xdb = 1` for retry. Status 3 resolves the TCP push channel, sets channel
`+0x20 = 1`, installs the receive callback at socket `+0x80` using GOT `0x2a8708`
= `iv_on_push_rcv_tcp_data`, and immediately sends certification (`0x2797f0`).
The periodic 200 ms certification timer is therefore **not the only first-send
source**. Other status values leave without that transition.

`iv_on_push_tcp_closed` (`0x27984c`, 280 bytes) resolves the node, clears its
`+0x38` socket and sets session `+0xdb = 1`. It neither sends hangup nor frees
the session in this function; the previously mapped disconnect timer handles
the retry/reset state. These callbacks retain the session pointer, so teardown
must unregister/cancel socket callbacks before releasing session ownership.
Lower-level `ivtcp` destruction and late-callback suppression remain unproven.

The mode-2 creation branch stores socket at node `+0x40` and passes GOT `0x2a8708`
(receive callback) directly to the connection factory. The common close callback
still clears `+0x38`. This asymmetry is observed, not a corrected or homologated
alternate path. Address-family/mode interpretation and callback-factory semantics
must be traced before adopting it. Do not generalize the other branch blindly.

## Correcting the score scheduling assumption

The score helper documented previously exists, but direct B/BL xrefs found no
caller of `iv_timer_calc_stat` (`0x27c6b0`), and `.rela.dyn` had no symbol entry or
relative addend for its address. This is a bounded static search, not proof that
an indirect/dynamically resolved caller cannot exist.

The actual ready-path timers in `iv_push_notify_connect_ready` use these resolved
`.rela.dyn` entries:

| Timer | Interval | Callback / GOT |
| --- | --- | --- |
| Channel `+0x298` | 1000 ms | `iv_on_timeout_statis_avdata` / `0x2a8618` |
| Channel `+0x2f8` | 10 ms | `iv_timer_send_rcv_rtc_data` / `0x2a8630` |
| Channel `+0x308` | E4 statistics interval ×1000 ms | `iv_timer_calc_push_stream_quality` / `0x2a8638` |

The last timer is conditional on a positive statistics interval. None is the
score callback. Consequently the score arithmetic must not yet be used to predict
live disconnect timing; its execution in this client path is unestablished.

## Bounded socket-free framer

`p2p/push_framing.py` implements the proven TCP envelope only: protocol 3, 20-byte
header, little-endian u16 body length at `+4`, nonempty body and total length at
most `0x8400`. It accepts fragmented/coalesced reads and retains only an incomplete
suffix. Local policies limit each feed to `0x8400` bytes and 256 emitted frames.
Working storage is bounded by the pending frame plus one admitted read and its
bounded output copies; no unbounded stream accumulation or background task exists.

Invalid headers, admission violations and partial EOF permanently close/clear
the decoder. It does not scan for a later magic byte or reuse buffered bytes after
reconnection. A feed containing a valid prefix followed by a malformed complete
header raises without returning the prefix. Callers must discard the connection.

This framer deliberately does **not** authenticate, check message-specific bodies,
infer successful certification or validate a checksum across unknown message types.
A future receiver must bind the socket to an authenticated relay/session and add
per-type validation before changing state. Tests cover every split point, bytewise
reads, coalescing, maximum length, malformed length/protocol, partial EOF, stale
decoder reuse and read/frame-count limits. No production callers were added.

## Remaining work

- Resolve terminal modes and the callback asymmetry at the `ivtcp` factory.
- Verify cancellation/ownership in socket destruction and queued callbacks.
- Trace the actual ready-path statistics callback before assigning score semantics.
- Keep pre-ready remote lifetime and safe relay release as live-diagnostic gates.

Inspections ran sequentially with 256 MiB/no-swap/50%-CPU/40-second caps;
the largest inspected callback run peaked at 40.8 MiB. No emulation or rebuild.
