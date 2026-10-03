# Native relay keepalive and local peer lifetime

2026-10-03, SDK 6.45 ARM64 binary pinned in
[platform provenance](yoosee-platform-sdk-versions.md). Offline disassembly only;
no SDK code, camera command, timer or relay connection was executed.

## Client-originated type 4

`iv_send_push_detect_frame` (`0x27a954`, 432 bytes) allocates/zeroes 44 bytes,
fills this layout, seals with the known four-word checksum, and calls
`iv_send_data_by_push_channel` at `0x27aad8`:

| Offset | Size | Value/source |
| --- | --- | --- |
| 0 | 1 | Protocol 3 |
| 1 | 1 | Type 4 |
| 2 | 2 | Zero |
| 4 | 2 LE | Body length 24 |
| 6 | 2 LE | Known rotated-word checksum XOR body length |
| 8 | 4 LE | Parent push-session ID |
| 12 | 8 LE | Terminal access ID (`+0x2e0`) |
| 20 | 4 | Zero |
| 24 | 4 LE | Channel `+0x18` sequence, before increment |
| 28 | 8 LE | Low 32 bits of `getTickCount64()`, zero-extended |
| 36 | 8 | Zero |

The sequence is loaded/stored at `0x27aa90–0x27aaa0`. The tick is written at
`0x27aa7c–0x27aa88`. The checksum covers only body bytes 20–27, **not** the tick,
access ID or push ID; it is not authentication. The separately mapped two-second
timer sends only on the selected channel in certified state 2. This packet must
not be used as a pre-authentication probe or evidence that certification passed.

`push_detect.py` implements only this outbound variant. The caller must supply
strict-width integers and an explicitly normalized tick; the encoder does not
read clocks, increment state, schedule, retry or send. Tests independently fix
the complete golden packet and checksum, boundary values and rejected types.

## Do not assume symmetric body interpretation

`iv_rcv_detect_frm_req` (`0x27b848`) treats incoming type-4 **body +8** (frame +28)
as a **user identity**, logs it and calls `iv_update_detect_time_push_live_user`
with that value plus a freshly sampled local tick at `0x27b8c0–0x27b8d0`.
The helper (`0x278e6c`) updates an existing matching user's local `+0x20` field;
it does not create a user or send a reply.

This differs from the outbound tick at the same offset. The direction/relay
transformation responsible is not established. Do not implement a shared inbound
decoder that assumes the sent tick will be echoed, or interpret this receive
handler as a certification/keepalive-ACK builder. Type-5 recertification-bit
handling remains documented separately in
[certification](yoosee-push-certification.md).

## Eight seconds is not a remote teardown guarantee

`iv_check_push_live_user` (`0x278c84`, 288 bytes) samples a low-32-bit local tick
and iterates the session's user list at `+0xe0`. A signed comparison of elapsed
tick against **8000 ms**, at `0x278cd8–0x278ce4`, removes/frees an entry only when
the difference is greater. It does not send hangup or free the push session.

A bounded direct B/BL scan found no caller of this helper. Its existence does
not establish scheduling, actual expiration timing, tick-wrap correctness or
relay/server behavior. In particular it cannot justify waiting eight seconds
instead of performing teardown, nor unblock a pre-ready live relay experiment.

The seven-module synthetic relay suite passed **145 tests**, peak 71.3 MiB,
without swap under a 256 MiB/50%-CPU cap. Ruff passed. Each symbol-sized RE call
used a 128 MiB/no-swap/50%-CPU cap, with observed peaks below 29 MiB. No production
caller or native-streaming capability was enabled.
