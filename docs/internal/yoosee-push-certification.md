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
absence here is not evidence of a proven whole-stack vulnerability.

## Remaining gate before a live diagnostic

1. Trace upstream UDP validation and `iv_send_data_by_push_channel` transport
   selection; retain strict expected peer/session correlation in any new code.
2. Map certification scheduling, retry limits and link-notify/counter transitions.
3. Establish pre-ready remote lifetime/cleanup: local timeout/free does not prove
   release at the relay or camera.
4. Then design a single-camera, time/byte/memory-bounded diagnostic with guaranteed
   local cleanup. Do not enable relay advertisement globally.

Disassembly was sequential with 256 MiB memory, no swap, 50% CPU and a 40-second
runtime ceiling; individual new inspections peaked below 28 MiB. The 77 combined
context/certification/teardown/platform tests passed at 48.1 MiB, no swap.
Camera-3 HD/platform identity and sustained native streaming remain unvalidated.
