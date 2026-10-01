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
Lower-level destruction is mapped below; suppression of already queued callbacks
across threads remains unproven.

The mode-2 creation branch stores socket at node `+0x40` and passes GOT `0x2a8708`
(receive callback) directly to the connection factory. The common close callback
still clears `+0x38`. This asymmetry is observed, not a corrected or homologated
alternate path. Address-family/mode interpretation and callback-factory semantics
must be traced before adopting it. Do not generalize the other branch blindly.

### Factory and local destruction follow-up

`ivtcp_comm_add_connect` (`0x1ede90`, 528 bytes) stores callback argument x3 at
socket `+0x70` (`0x1edf1c`) and context x5 at `+0x90`. Thus the mode-2 callback
asymmetry is real at registration, not a disassembler guessing the wrong parameter.
Its reconnect policy at `+0x5c` is 1 when argument w2 is zero (as in push setup),
2 otherwise. It calls `ivtcp_start_connect`, then links successful allocations
into the communication object's list. Its address copy checks sockaddr family 2
for a 16-byte copy, otherwise 28 bytes; that does not by itself establish the
meaning of the separate terminal-mode enum.

`iv_push_rly_node_free` (`0x278494`, 564 bytes) iterates eight session node slots.
For each present socket (`+0x38`, then `+0x40`) it sets socket policy `+0x5c = 0`,
calls `ivtcp_close_socket`, then `ivtcp_close_notify`, and clears the node pointer.
Only afterward does it free node receive storage and the node itself.

`ivtcp_close_socket` (`0x1eca2c`, 316 bytes), for an open descriptor in status 2/3,
flushes/disables the bufferevent, calls shutdown, frees the bufferevent, closes the
descriptor and sets it to -1; it then sets socket status to 1. This is local
resource cleanup, not a wire-level relay hangup acknowledgement.

`ivtcp_close_notify` (`0x1ecb68`, 444 bytes) invokes the close callback **before**
branching on policy. Policies 0/1 unlink and call `ivtcp_session_free`; policy 2
requeues and calls `ivtcp_start_connect`. Clearing policy in node teardown prevents
this internal reconnect branch, but does not suppress the close callback itself.
The callback can therefore still touch the parent push session during cleanup;
that parent must remain alive until node cleanup returns.

`ivtcp_session_free` (`0x1ed5b8`, 136 bytes) frees any remaining bufferevent and
deletes/frees its event before freeing the socket object. A nonzero `event_del`
result returns early instead of freeing the socket object. The inspected path
establishes local ordering, not a proof of race-free destruction under every
event-loop/thread condition. Our future transport should use explicit cancellation
and connection generations, not reproduce implicit pointer ownership.

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

### Event dispatcher follow-up and local generation guard

`ivtcp_start_connect` (`0x1ed00c`, 1452 bytes) creates a nonblocking socket and
`bufferevent_socket_new` with option argument zero (`0x1ed404`). It registers
`iv_session_cb_read` and `iv_session_cb_event` through GOT `0x2a8418`/`0x2a8420`
at `0x1ed538`, with the socket object as callback context. Output evbuffer locking
is enabled separately; this is not evidence that parent-session access is locked.

`iv_session_cb_read` (`0x1ec748`, 592 bytes) invokes socket callback `+0x80`, or
the communication object's fallback. Positive return can resume paused reads;
-2 pauses reads and schedules an event; other negative values except -3 close
and notify. Thus missing push-channel return -2 is handled as backpressure here,
not necessarily permanent rejection. Our diagnostic must not inherit that retry
behavior for malformed/uncorrelated protocol input.

`iv_session_cb_event` (`0x1ecd24`, 744 bytes) handles raw event bit 4 by draining
pending output then closing/notifying. Bit 5 handles errors: connected status 3
closes/notifies, while connecting status 2 invokes socket callback `+0x70` and
then reconnects only for policy 2, otherwise unlinks/frees. Bit 7 calls socket
initialization and helper `0x1ed7e8`; bit 6 can pass buffered input to the read
callback. These functions carry raw socket/session pointers, without an explicit
connection-generation check in the inspected bodies. This is not proof of a
native use-after-free: event-loop cancellation semantics still matter.

`p2p/push_reception.py` now provides our own **offline**, single-event-loop
generation guard around framing. Each `begin()` returns an opaque object captured
by that connection's callbacks, aborts old partial bytes and creates a fresh
decoder. Stale reads/EOF are ignored before parsing; malformed current reads,
EOF and explicit cancellation invalidate the current generation. No old callback
can close or complete the replacement decoder through this API. Decoder abort
is idempotent and clears partial input.

This is local isolation, not authentication, thread synchronization or actual
socket/task cancellation. It does not revoke frames already handed to downstream
consumers; any asynchronously queued downstream work must also carry connection
identity and be cancelled/rechecked before changing state. The future transport
must own socket shutdown and task cancellation explicitly. No production caller,
success-state transition, retry loop or camera command was introduced.

Synthetic tests cover old data, old malformed reads, old EOF, unrelated receiver
tokens, cancellation, partial EOF, clean EOF and recovery with a new generation.

### Open gates

- Resolve terminal modes and the callback asymmetry at the `ivtcp` factory.
- Local socket destruction order is mapped; queued callback cancellation and
  thread/event-loop ownership still require proof before live transport integration.
- Trace the actual ready-path statistics callback before assigning score semantics.
- Keep pre-ready remote lifetime and safe relay release as live-diagnostic gates.

Inspections ran sequentially with 256 MiB/no-swap/50%-CPU/40-second caps;
the largest inspected callback run peaked at 40.8 MiB. No emulation or rebuild.
