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

2026-10-03: `is_current(generation)` now lets downstream queued consumers recheck
the **captured original** token immediately before a synchronous state change.
Recheck after each await; this is not a cross-thread lock, automatic queue
cancellation or authentication. Parsed bytes remain readable after retirement,
but consumers can reject their expired ownership. Tests cover replacement,
cancellation, clean/partial EOF, malformed input and foreign/absent generations.
The combined framing/context/certification/teardown/reception set passed 97 tests
under 256 MiB/no-swap/50%-CPU limits, with 99.2 MiB peak; Ruff passed.

### Actual ready-path quality timer (2026-10-03)

`iv_timer_calc_push_stream_quality` (`0x27e530`, 164 bytes) invokes
`iv_calc_push_stream_quality` at `0x27e58c`. When sample count at channel `+0x328`
reaches 16, it calls `iv_report_push_stream_quality` with arguments `(channel, 0, 1)`.
It does not invoke the previously mapped `iv_timer_calc_stat` score helper.

The collector (`0x27e1a4`, 908 bytes) bounds collection to 16 records of 24 bytes
at channel `+0x1a8`. Its observed record fields are:

| Offset | Source/operation |
| --- | --- |
| 0 | UDP: object `channel +0x358`, field `+0x38`; TCP: selected node socket `+0x38`, field `+0x6c`. These counter meanings need separate writer provenance. |
| 4 | UDP object field `+0x64`; TCP `ivtcp_get_sndbuf_datalen(socket)`. |
| 8 | Delta of channel `+0x19c` versus snapshot `+0x190`; log calls it `block_times`. |
| 12 | Delta of `+0x1a0` versus `+0x194`, divided by elapsed whole seconds; log calls it `rate_data_send`. Units are not established here. |
| 16 | Delta of `+0x1a4` versus `+0x198`, divided by the same interval; log calls it `rate_data_recv`. |
| 20 | Low 32 bits of `time(NULL)`. |

Elapsed seconds use the low 32 bits of `getTickCount64()` minus channel `+0x330`,
divided by 1000; a zero result is replaced by one. Counter/tick wrap behavior is
not validated and must not be copied uncritically. The three counter snapshots
are copied, sample count incremented and tick baseline updated afterward.

The report helper (`0x27d9bc`, 2024 bytes) calls `iv_gutes_add_send_pkt` at
`0x27e168`; it is not merely local logging. Its packet schema/destination are
not yet mapped. No equivalent reporting traffic is implemented. This closes the
ready-timer identity question, **not** the separate score scheduling or remote
lifetime gates, and provides no platform enum or HD resolution selection.

### Open gates

Callback-signature checkpoint: connected-event helper `0x1ed7e8–0x1ed8c8`
invokes socket `+0x70` at `0x1ed894` with only x0 (socket) explicitly supplied;
the connection-error path likewise supplies only x0 at `0x1ece98`. This matches
`iv_on_push_tcp_connect_finished`, but the previously identified mode-2 registration
points to `iv_on_push_rcv_tcp_data`, whose entry consumes x0/x1/w2 (socket, input
buffer, length). The inspected caller does not prepare those extra arguments.
The mismatch is static evidence against copying that mode-2 branch, not proof
that a deployed camera/emulator reaches it or that it caused previous crashes.
Do not run the SDK branch to "see what happens" or infer the mode from camera model.

Response-validation limit: the available `iv_rcv_certify_frm_rsp` consumer does
not establish a success status/body schema; no push certification-response builder
was found in the exported-symbol search. This is not an exhaustive binary search.
No response parser or checksum-success shortcut was invented. Certification remains
unconfirmed until peer/session correlation and message-specific response semantics
have independent evidence; local generation tokens cannot provide that evidence.

- Terminal address-family modes and the callback mismatch are now mapped below;
  runtime reachability and safe alternate transport integration remain unproven.
- Local socket destruction order is mapped; queued callback cancellation and
  thread/event-loop ownership still require proof before live transport integration.
- Trace the actual ready-path statistics callback before assigning score semantics.
- Keep pre-ready remote lifetime and safe relay release as live-diagnostic gates.

Inspections ran sequentially with 256 MiB/no-swap/50%-CPU/40-second caps;
the largest inspected callback run peaked at 40.8 MiB. No emulation or rebuild.

## Relay descriptor construction and transport flags (2026-10-03)

`iv_push_session_add_tcp_rly` (`0x279e5c`, 1352 bytes) receives the session,
IPv4 descriptor and IPv6 descriptor in x0/x1/x2. The constructor establishes:

| Source | Destination / evidence |
| --- | --- |
| IPv4 descriptor `+0x0a` | Host-order u16 port, byte-swapped into node `+0x0a` at `0x279ffc–0x27a00c`. Zero port skips this address branch. |
| IPv4 descriptor `+0x0c` | Four address bytes copied to node `+0x0c`; node `+8` receives family 2 at `0x279ff0–0x279ff4`. |
| IPv4 descriptor `+8` | Bits 4–7 populate node cluster ID at `0x279fc4–0x279fd8`. |
| IPv6 descriptor `+0x0a` | Host-order u16 port, byte-swapped to node `+0x1a` at `0x27a094–0x27a0a8`. |
| IPv6 descriptor `+0x0c` | Sixteen address bytes copied to node `+0x20`; node `+0x18` receives family 10 at `0x27a088–0x27a0e0`. |

For the first added node, bit 0 of the **IPv4 descriptor** flags at `+8` selects
`iv_create_push_tcp_channel` (`0x27a1c0–0x27a1d8`). If that branch is not taken,
bit 1 selects a UDP channel (`0x27a2a0–0x27a2bc`). These are advertisements and
selection inputs, not evidence of certification or reachability. The analogous
IPv6 flags' meaning is not established: this function still reads the IPv4
descriptor for transport selection. Do not infer family pairing by table index.

Together with the earlier connect function, this proves node socket `+0x38`
uses its IPv4 sockaddr and `+0x40` its IPv6 sockaddr. Terminal mode 2 chooses
the IPv6 branch in that function; its assignment is mapped below. The
callback-signature mismatch is not resolved by naming the address.

`push_relays.py` now extracts descriptors from correlated, bounded E4 envelopes
without sockets, DNS, selection, persistence or runtime callers. Ports remain
zero when advertised zero. It interprets TCP/UDP/cluster bits only for IPv4;
IPv6 properties return unknown. Local policy admits at most eight entries per
family and rejects trailing extensions. This limit is not a vendor capacity
claim. Addresses are omitted from repr, but dataclass serialization needs
redaction; future connectors still require authenticated provenance and explicit
endpoint policy, particularly for local/special addresses.

The combined six-module relay test set passed 118 synthetic tests (70.1 MiB
peak, no swap, 256 MiB/50%-CPU cap); Ruff passed. No build/restart or camera test
was needed. Independent frame/certification evidence is still required before
connecting to any parsed address.

## Terminal mode assignment (2026-10-03 follow-up)

`gat_on_rcvpkt_LIST_RESP` (`0x246bdc`) stores its terminal argument x0 at stack
`+0xa8` and packet argument x1 at `+0xa0`. The receive context's sockaddr begins
at packet `+0x18`. Once an existing list server is found, the code at
`0x246e10–0x246eb0` updates terminal `+0xa0` as follows:

| Received sockaddr family | Previous terminal mode | New mode |
| --- | --- | --- |
| 2 (IPv4) | 2 or 3 | 3 |
| 2 (IPv4) | Otherwise | 1 |
| Non-2 branch (IPv6 path) | 1 or 3 | 3 |
| Non-2 branch (IPv6 path) | Otherwise | 2 |

Thus 1/2/3 represent IPv4/IPv6/both-family observations on this path, not camera
models or platform versions. The native branch tests **non-2**, not explicitly
family 10; a future implementation must reject unsupported address families
rather than copying that permissive else branch. This local network state is
not proof of camera capabilities or a trustworthy remote endpoint.

The separate receive path at `0x21a810–0x21a8b8` writes mode 1 and clears counter
`+0xa4` for a family-2 socket. For its other-family branch it increments that
counter and sets mode 2 only when the previous mode is zero and the count reaches
six. Do not mistake that observation counter for a retry timer. The containing
local function is stripped; the nearest exported `iv_comm_exit` label is not its
identity. `iv_reset_network` clears terminal mode/counter at `0x1daecc/0x1daed4`;
`iv_unit_init` also initializes mode zero at `0x2386d4`.

This connects the mode-2 callback mismatch to the IPv6-selected relay branch,
without establishing runtime reachability or a cause for any emulator crash.
Mode 3 chooses the IPv4 connect branch here; the send helper has separately
mapped socket fallback behavior. No network reset or SDK function was executed.

### Analysis resource correction

An attempted whole-section Capstone scan hit its **256 MiB cgroup limit** and was
OOM-killed inside that isolated unit (no swap). The replacement scanned only the
native subsystem `0x1d0000..0x280000` in **1 KiB instruction blocks**, under a
smaller 128 MiB cap; it completed at 26.6 MiB peak. Subsequent address-range reads
also stayed below 27 MiB. All containers remained running and host swap stayed
zero. Use bounded chunks for future scans: Capstone's iterator-looking API must
not be assumed to allocate lazily over a large supplied buffer.
