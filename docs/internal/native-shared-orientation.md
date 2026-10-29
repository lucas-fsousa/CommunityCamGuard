# Orientation on the native streaming session

The driver now routes the existing normal/inverted control through the native
owner. The HTTP contract and per-device capability/profile permissions are
unchanged. No second authentication, vendor app or raw command endpoint is added.

The volume-specific D2 job/inbox/pump were renamed to `model_control_*` and now
accept a closed union of `VolumeWriteSpec` and `OrientationWriteSpec`. Those two
types select existing fixed protocol builders; callers cannot supply arbitrary
model paths or scalar values. They share one inbox, account generation and
sequence allocator with media, reads, heartbeat and PTZ. Scalar confirmation
logic is factored into `shared_control_write`, not duplicated per feature.

Orientation preflight reads the fixed `ProWritable.videoParm` root and accepts
only `multiFlip=1` (normal) or `3` (inverted). An unchanged state sends no D2.
A change sends D2 once, requires its D3 result and bounded exact readback; failures
do not repeat the write or fall back to another login. The identity-only mailbox
still excludes this root, and no mailbox accepts `.setVal` or action paths.
No additional public read capability is inferred from an existing write proof.

179 focused tests and Ruff passed under192MiB/no swap (110.1MiB peak); Mypy376
passed under256MiB. These cover both typed writes, one-slot exclusion, rejection,
missing preflight, unchanged state, exact readback and no fallback login.

Deployed `9784dd2`, build `b-935096facb0c`: camera3 was confirmed normal by the
ordinary control API's no-op preflight. It then accepted normal→inverted→normal;
both changes returned HTTP200, verified, D3 result0, transport/application ACKs
and exact raw readback. The same MSE socket stayed open and advanced from3 to7
binary fragments throughout. The native source remained active revision35;
speaker volume still read50 and all three cameras reported online/recording.
No sound, light or motor movement was sent; only camera3's image orientation was
changed and restored. This is protocol/readback acceptance, not a new human
visual confirmation. GitHub CI and Image passed for the implementation.
