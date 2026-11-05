# Continuous TCP MTP maintenance

Static evidence from the pinned SDK6.45 ARM64 `libiotvideomulti.so`:

- `iv_mtp_session_meter_proc` (0x259490) invokes
  `iv_mtpSession_send_meter_proc` and route optimization, returning adaptive
  timer intervals during initial measurement.
- `iv_on_timer_mtpsession_proc` (0x260e6c) also invokes meter sending and
  optimization for established session roles2/3.
- `iv_mtpSession_send_meter_proc` (0x259760) iterates the session's channels
  and calls `iv_mtp_chnnel_send_meter_frm` (0x258758) with `getTickCount64`.
- ARM64 PLT relocation resolution confirms0x297d10=send_meter_proc,
  0x297d00=optimize_proc and0x298170=send_meter_frm.

The continuous Python transport previously sent only its initial measured
request. It serviced incoming meter requests but had no periodic outgoing
maintenance after that initial exchange. This is an actual SDK/runtime gap,
not proof that every earlier timeout had that cause.

`mtp_tcp_heartbeat.py` now owns this one responsibility on the same measured
connection:1s local cadence, one pending sequence/full64-bit timestamp,3s receipt
window, fresh sequence after expiry, and no catch-up bursts. These are bounded
local policy, not a clone of the SDK's multi-route scheduler. It reuses the
already-proven measurement builder and exact identity/sequence/timestamp ACK
matcher. First valid periodic receipt is logged with only its numeric sequence.

No new socket, account, authentication renewal, camera action or media buffer is
created. Traffic uses the existing rate budget and cooperative output batch.
Maintenance starts after the initial correlated measurement and stops before
AVCLOSE. Finite diagnostic behavior is unchanged. A meter receipt never grants
AV readiness, extends media-progress deadlines or keeps a silent video source
alive. Stale/wrong/checksum-invalid ACKs are ignored.

85focused tests passed under192MiB/no swap/50%CPU, peak100.3MiB. They cover
periodic receipts, wrong identities/flags/sequence/timestamp, expiry without
replay, clock validation, no burst after a time jump, unchanged5s media idle
failure despite valid heartbeat ACKs, and maintenance cancellation before CLOSE.
Ruff and Mypy405 passed. Live periodic-receipt and sustained-media acceptance
follow deployment; neither is inferred from these fixtures.

Live checkpoint: deployed `3b0b311` / `b-58c43b444dfd`, active144. The camera
returned the correctly correlated periodic receipt for sequence2 on the existing
connection. CI6012tests/1skip and Image passed. MSE and original300s archive
rollover continued, with no sound/light/motor command. A 900-second continuous
MSE observation completed without reconnection or source recovery. This proves
that observation window, not multi-day endurance; the
transport maintenance receipt alone is not the AV liveness evidence.
