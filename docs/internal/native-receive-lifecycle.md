# Native receive ownership — 2026-09-14

## Implemented, socket-free

`drivers/yoosee/p2p/receive_lifecycle.py` owns one pinned inbound MTP/KCP
conversation from initialization through active consumption. `activate()` changes
only the phase: sequence numbers, acknowledged out-of-order segments, partial
messages and their original assembly deadline remain in the same receiver.

Messages received during initialization are returned immediately, not discarded or
queued for a later consumer. The eventual caller must feed these into the same
correlated TLV/V1 parser used after acceptance. Activation is an explicit caller
decision after verified AV acceptance; this module does not authenticate it.

This addresses an integration hazard in the current `av_session.py`: that existing
initializer acknowledges inbound PUSH segments and returns sequence summaries,
but does not transfer partial message/reordering state. Reconstructing a video
receiver from that summary would lose already acknowledged bytes. **The existing
initializer and validated intercom were not changed.** The new module has no
production caller yet and does not claim to have corrected a live-stream issue.

## Bounds and failure handling

- One receiver: at most 128 segments and 256 KiB retained KCP payload.
- No socket, worker, subprocess, frame queue, decoder or reconnect loop.
- Default initialization/progress limits: five seconds; absolute experimental
  lifetime: 30 seconds, configurable only up to 60 seconds.
- Two-second partial-message deadline survives activation. The owner must poll
  during silence; no background timer is started.
- Only complete newly emitted messages refresh progress. Duplicates, wrong peers,
  wrong conversations, invalid packets and transport ACKs cannot refresh it.
- Expiry or fatal KCP failure closes and clears the receiver. The eventual owner
  must also close the real socket and discard parser/decoder state. This module
  cannot close resources it does not own.
- A replacement must come from a fresh negotiation, never guessed next-sequence
  values or reuse of the failed channel. Pinning is not cryptographic authentication.

These are short experimental limits, not production timeout policy. Message
progress alone is not proof of video progress: the future owner must also enforce
codec/keyframe deadlines and correlated media acceptance.

## Evidence and next integration

Synthetic tests exercise partial and out-of-order messages spanning activation,
early complete-message delivery, duplicate suppression, assembly expiry across
handoff, conflicting duplicates, repeated activation, noise-resistant progress
expiry, absolute initialization/lifetime limits, terminal closure and a fresh
conversation rejecting old-channel traffic. No camera was contacted or actuated.

All 18 new tests passed, along with the full Python regression suite (Node wrapper
excluded and its camera-panel/PTZ contracts run separately), Ruff and Mypy.

Next: build an experimental AV initializer that owns this receiver **before its
first ACK**, correlates ACCEPT and the negotiated conversation/cookie, and feeds
complete messages into a continuous bounded TLV/V1 parser. Preserve current
intercom golden frames and isolate this from production source selection. Then
test bounded camera-3-only reception, keyframe recovery, pacing/A-V synchronization
and single-producer handoff. No dashboard capability should be enabled on the
basis of these synthetic tests.
