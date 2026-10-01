# Push/relay certification — SDK 6.45, offline evidence

2026-10-01. Binary identity: [SDK provenance](yoosee-platform-sdk-versions.md).
Continues [E4 context and teardown](yoosee-push-teardown.md). No relay was contacted,
camera commanded or production transport enabled.

## One-terminal request

`iv_send_push_certify_frame` (`0x27a474`, 800 bytes) allocates `36 + token_length`.
The recovered branch fixes terminal count to one. Fields are little-endian:

| Offset | Width | Meaning/source |
| --- | --- | --- |
| 0, 1 | bytes | Protocol 3, type 6 |
| 2–3 | 2 | Zero in this branch |
| 4 | u16 | Body size `16 + token_length` |
| 6 | u16 | Four rotated body words XOR body size; same helper as hangup |
| 8 | u32 | Session push ID (`+8`) |
| 12 | u64 | Terminal access identity (`+0x2e0`) |
| 20 | byte | Session `+0x22`; SDK log calls it token type |
| 21 | byte | Terminal count = 1 |
| 22 | u16 | Token length + 8 |
| 24 | u16 | Token length |
| 26 | u16 | Local short session ID (`+0xdc`) |
| 28 | variable | Token from session descriptor `+0x28` |
| 28 + token length | u64 | Session `+0x48`: E4 `+0x38` device ID |

The last provenance follows the 96-byte copy from E4 `+0x28` to session `+0x38`
in `iv_push_session_app_new`. Send dispatch goes through
`iv_send_data_by_push_channel` at `0x27a768`, unlike hangup's explicit UDP call.
Do not substitute a broker or MTP request for this frame.

`p2p/push_certification.py` encodes only this variant, with shared integer bounds
and checksum in `_push_wire.py`. Its 2–4096-byte token policy is local, including
the floor needed for safe hangup; it is not a discovered vendor requirement.
Synthetic vectors independently calculate the checksum and test malformed fields
and boundaries. No production caller sends these bytes.

## Receive dispatch and state changes

`iv_rcv_push_data` (`0x27a794`, 300 bytes) dispatches by frame byte 1:

| Type | Handler |
| --- | --- |
| 4 | `iv_rcv_detect_frm_req` |
| 5 | `iv_rcv_detect_frm_rsp` |
| 7 | `iv_rcv_certify_frm_rsp` |
| 8 | `iv_rcv_rtc_frame` |
| 13 | `iv_rcv_link_notify_frm_rsp` |

There is no explicit hangup-ACK case in **this dispatcher**, not proof of global
absence of acknowledgements elsewhere.

`iv_rcv_certify_frm_rsp` (`0x27b470`, 296 bytes) updates receipt time, sets channel
state `+0x20 = 2` at `0x27b560` and promotes session state `+0xdb` from 2 to 3.
It does not inspect response status or verify checksum in this function. Do not
copy it as a standalone trust decision. The timer subsequently recognizes this
certified state and marks ready as described in the teardown document.

`iv_rcv_detect_frm_rsp` (`0x27b598`, 312 bytes) reads bit 0 at frame `+20`.
When set, it resets session state `+0xdb` to 1, generates a new local short ID
at `+0xdc` (`0x27b680`) and sets channel state `+0x20 = 1`. The SDK log describes
resending certification. A future transport must use the current short ID for
certification **and** hangup; an initial cached ID can become stale.

## Outer receive checks recovered so far

TCP: `iv_on_push_rcv_tcp_data` (`0x279964`, 1128 bytes) first resolves a known node
and push channel. It waits for a 20-byte header, requires protocol byte 3 and a
nonzero u16 body length, limits total frame size to `0x8400`, and waits for the
complete declared frame. It removes one frame and dispatches it, looping over
additional complete frames. This is not a packet-per-read protocol. No checksum
check appears in this function before dispatch.

UDP: `iv_rcv_udp_push_pkt` (`0x256710`, 444 bytes) resolves the outer channel by
frame `+8` push ID and requires an existing push session. Type 8 takes the separate
KCP path. Other types resolve a known node using source IPv4 and select its UDP
push channel before dispatch. This function itself does not establish source-port,
checksum or access-identity validation. Upstream UDP dispatch remains to audit;
absence here is not evidence of a proven whole-stack vulnerability. Follow-up:
the `recvfrom` dispatch at `0x21a468–0x21a568` branches on protocol byte 3 directly
to `iv_rcv_udp_push_pkt`, before the subsequent GAT length checks. No checksum or
declared-body-length check appears on that inspected branch. The xref tool labels
this range with nearest symbol `iv_comm_exit`; that is not a reliable function name
for this stripped local receive callback. Do not copy its permissive validation.

## Scheduling, transport selection and errors

E4 setup creates these timers at `0x24e03c–0x24e0f4`. Callback identities were
resolved from `.rela.dyn` entries rather than guessed from interval order:

| Channel timer | Interval argument (ms) | Callback / GOT relocation |
| --- | --- | --- |
| `+0x2d8` | 100 | `iv_timer_check_push_live_connect` / `0x2a85c8` |
| `+0x2e0` | 100 | `iv_timer_check_push_live_disconnect` / `0x2a85d0` |
| `+0x2e8` | 200 | `iv_timer_send_push_certify_frame` / `0x2a85d8` |
| `+0x2f0` | 2000 | `iv_timer_send_push_detect_frame` / `0x2a85e0` |

`iv_timer_send_push_certify_frame` (`0x255e7c`, 336 bytes) iterates channels and
sends only for channel state 1. It logs nonpositive send results without a local
attempt counter. Initial connect has the separate >5000 ms timeout; do not infer
an exact packet count from timer intervals or an unlimited diagnostic retry budget.
`iv_timer_send_push_detect_frame` (`0x255fcc`, 272 bytes) sends only for the selected
channel in state 2 (certified).

`iv_send_data_by_push_channel` (`0x27e6ac`, 648 bytes) uses channel byte `+8`:
0 selects TCP, 1 selects UDP; other types return -11. Missing input returns -10.
TCP chooses node socket `+0x40` for terminal mode 2, prefers `+0x38` then `+0x40`
for mode 3, otherwise `+0x38`. UDP uses terminal socket `+0x70` with node address
`+0x18` in mode 2, otherwise `+8`. Address-family interpretation of terminal modes
still needs explicit provenance; this table intentionally does not guess it.

`iv_rcv_link_notify_frm_rsp` (`0x27b6d0`, 376 bytes) reads subtype at frame `+30`:
0 inserts a live peer using u64 at `+20`; 1 sets session `+0xdb = 4`, logging a
peer-user limit. Other subtypes only log here. Do not report type 13 as generic
success: it can signal a capacity refusal.

`iv_timer_check_push_live_disconnect` (`0x255c78`, 516 bytes), active for outer
channel status 4, resets if the connected predicate fails. Session state 4 writes
error `0x4e27` then resets. State 1 enters KCP/reconnect handling; retry byte
`+0x350 > 4` writes `0x4e28` and calls reset. The native function continues after
that call, so it should not be copied mechanically as a safe retry loop. TCP
reconnect attempts are gated by elapsed time >2000 ms, generate another short
session ID, increment the retry byte and update attempt time. This further
confirms that a saved initial short ID is not valid indefinitely.

## Connected predicate is a score, not a timeout counter

`iv_timer_calc_stat` (`0x27c6b0`, 872 bytes) updates selected-channel `+0x1c`
using `iv_calc_score` (`0x27e9bc`, 384 bytes) at `0x27c764–0x27c770`. It supplies
the old score and two 20-byte statistic snapshots (`+0x50`, `+0x3c`), then copies
`+0x3c` into `+0x50`. The SDK logs explicitly name the field `score`.
`iv_pick_push_channel` initializes it to 75 at `0x27b078`; the connect timer also
initializes a certified selected channel to 75. The connected predicate tests >5.

For a finite defined ratio, the score helper computes `100 * delta(+12) /
delta(+8)` from its two snapshot arguments, then applies the first matching rule:

| Ratio | Score delta |
| --- | --- |
| >80 | -10 |
| >60 | -8 |
| >50 | -5 |
| >30 | -3 |
| >10 | +5 |
| Otherwise | +15 |

It clamps the result to 0–75. There is no zero-denominator guard in this helper;
do not invent inactivity timeout semantics from it or copy it without validating
the statistic sources and floating-point edge cases. The packet-counter writers
and scheduling of this statistic callback remain to trace. The earlier term
“counter refresh/decay” refers to this now-identified score, not elapsed seconds.
Follow-up: the ready-path timers do **not** register `iv_timer_calc_stat`, and no
direct caller/relocation was found in the bounded search. Its execution on this
path is unproven; see [callback and reception evidence](yoosee-push-reception.md).

## Remaining gate before a live diagnostic

1. Enforce independent strict receive framing, checksum, expected peer and session
   correlation in a future transport; map terminal address-family modes explicitly.
2. Map statistic sources/scheduling and reconnect callback ownership. The connected
   score, timer, retry and link-notify branches are mapped, not runtime-validated.
3. Establish pre-ready remote lifetime/cleanup: local timeout/free does not prove
   release at the relay or camera.
4. Then design a single-camera, time/byte/memory-bounded diagnostic with guaranteed
   local cleanup. Do not enable relay advertisement globally.

Disassembly was sequential with 256 MiB memory, no swap, 50% CPU and a 40-second
runtime ceiling; individual new inspections peaked below 29 MiB. The 77 combined
context/certification/teardown/platform tests passed at 48.1 MiB, no swap.
Camera-3 HD/platform identity and sustained native streaming remain unvalidated.
