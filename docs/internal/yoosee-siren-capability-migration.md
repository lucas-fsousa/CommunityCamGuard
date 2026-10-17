# Siren capability migration

Status (2026-10-16): camera-3 correlated identity/state read verified; optional bounded
collection implemented. Strict actuation is not opted into production capability rollout.

## Camera-3 read acceptance, 2026-10-16

One session returned complete ProConst identity and a correlated successful
`Action.expelCtrl` response: timestamped state 1 (OFF), readable evidence supported.
All three reads had transport ACK and application error code 0. The successful
exchange took 3.2 seconds; process peak 41.7 MiB, no swap. An earlier attempt failed
with P2PProbeError before yielding observations; its specific reason was not retained.
This is not evidence of audible output or guaranteed cache freshness. No AC actions,
audio, light changes, token renewal, profile registration or database writes occurred.

`collect(..., include_siren=True)` / `collect_snapshot(..., include_siren=True)` now
allow the additional B7 read within the existing single-session 20-second budget.
Default collection remains unchanged. Normalization binds the optional observation to
the same exact device/identity and rejects duplicate or cross-device roots. Missing or
failed evidence cannot authorize a pulse; operation profiles remain independently required.
No rule-version bump is necessary: existing evidence semantics and default snapshots
are unchanged, and a missing optional feature resolves to unknown.
The local operator script is ignored `re/check_camera3_siren_read.py`; it has a fixed
test-camera association, read-only database access and sanitized output only.
Validation: 87 focused collector/snapshot/store/policy/correlation tests passed
(102.4 MiB peak, no swap), plus Ruff. No rebuild or production rollout in this step.

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

1. DONE: camera-3 correlated state/identity read and opt-in normalized collection.
   Automatic collection remains disabled.
2. Register an exact-unit operation
   profile only after a controlled strict pulse with confirmed OFF cleanup. Prove each duration
   before advertising it. Physical audibility is separate from ACK/readback success.
3. Opt in that unit/control explicitly, keeping other cameras untouched.
4. Audio migration is separate: current `audio.supported` accepts local RTSP coordinates or P2P
   enrollment. Those are connection prerequisites, not speaker capability proof. Model/firmware,
   exact unit, recorded-message/hold-to-speak operation and RTSP versus P2P route need independent
   provenance. Do not turn the physically verified RTSP path into an unproven P2P permission.
   Preserve the homologated PCM framing/pacing while replacing the capability gate.
