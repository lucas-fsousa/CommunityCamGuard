# Siren capability migration

Status (2026-09-11): strict transport/evidence boundary implemented and tested offline;
not opted into production capability rollout. No live camera calls in this step.

## Separate claims

`Action.expelCtrl` is the deterrent state/action, not `ProWritable.resFile` sound selection,
speaker volume, floodlight, person detection or two-way audio. None certifies another.
Existing sound-selection proofs must not authorize a siren pulse.

`siren_evidence.py` accepts only the exact root containing integer `t` in 1..2^31-1 and integer
`stVal` equal to 1 (off) or 2 (on). Timestamp -1 denotes unsupported; missing, zero, boolean,
floating-point or nested state remains unknown. An unrelated integer/timestamp cannot become
an OFF preflight. Both valid states indicate readable state support, never successful actuation.

## Strict opt-in transport

`pulse_camera_siren(require_correlated_response=True)` requires the enrolled device to match
the opened session, correlated B7 preflight/final readback and the exact timestamped state.
AC/AD handling additionally checks encrypted mode 2 and access-node session. Transport ACKs
match the outgoing sequence; AD application results match the random message ID. A transport
receipt alone does not confirm application success. No new guesses about AD payload fields.

Single-shot ON, duration bounds, unconditional explicit OFF (including failed activation) and
the independent cleanup deadline remain intact. Strict parsing also applies to final OFF;
ambiguous readback fails the operation. Network cleanup remains best effort, not a guarantee
that a disconnected device stopped sounding. The compatibility default is unchanged while the
new mode awaits exact-unit verification. Session target mismatch is rejected in either mode.

Validation: synthetic encrypted replies and mocked pulses; stale session/message/sequence,
unencrypted frames, wrong peer, malformed state, strict preflight, single ON and unconditional
OFF coverage. No sockets to cameras, Android/emulator, SDK decompilation or container rebuild.
The full 1,320-test suite, Ruff and mypy (156 source files) passed with bounded test memory.

## Next bounded steps

1. Camera 3 only: read `Action.expelCtrl` with correlated B7 alongside the complete ProConst
   identity. Record sanitized provenance; do not add automatic collection before verification.
2. Add normalized siren evidence to bounded collection, then register an exact-unit operation
   profile only after a controlled strict pulse with confirmed OFF cleanup. Prove each duration
   before advertising it. Physical audibility is separate from ACK/readback success.
3. Opt in that unit/control explicitly, keeping other cameras untouched.
4. Audio migration is separate: current `audio.supported` accepts local RTSP coordinates or P2P
   enrollment. Those are connection prerequisites, not speaker capability proof. Model/firmware,
   exact unit, recorded-message/hold-to-speak operation and RTSP versus P2P route need independent
   provenance. Do not turn the physically verified RTSP path into an unproven P2P permission.
   Preserve the homologated PCM framing/pacing while replacing the capability gate.
