# Siren capability migration

Status (2026-10-18): camera-3 strict two-second pulse physically confirmed (including
stop), following the later authorized retest below. Runtime wiring is implemented;
the exact-unit operation profile and rollout have not yet been activated.

## Runtime wiring, 2026-10-18

Demand-driven refresh now rereads explicit rollout selection in its existing single
worker and includes `Action.expelCtrl` only for the selected control and matching
enrolled native device. It uses the existing session/budget; no extra worker or
action is introduced. Other units/controls retain the default collection.
The control dispatcher requests strict transport for explicitly migrated siren
controls; legacy dispatch remains unchanged. Selection alone is not authorization:
the normal capability intersection must still supply exact-unit operation proofs,
current identity/evidence and individually proven duration options.

62 focused tests passed (107.2 MiB/no swap), including dispatch, optional collection,
wrong-device exclusion, and a two-second-only proof that cannot advertise five/ten
seconds. Tests use synthetic data, never real sound. This stage does not register a
profile, activate rollout, or claim live acceptance of the integrated dashboard path.

Activation must preserve existing operation/source records and dynamic-resource
rollout, add only the independently proven two-second option, and obtain a fresh
snapshot containing siren evidence. An older otherwise-current snapshot omits this
optional root; it cannot authorize the new control. Do not repeatedly pulse the
siren merely to repeat the already completed physical validation.

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

Latest authorized retest: two-second pulse passed protocol ON/OFF/final-state and
operator sound/stop confirmation at requested 50% speaker volume. Earlier failures
remain unattributed. See [results and concurrency fix](camera3-controls-followup-2026-10-16.md).

### Controlled pulse follow-up, 2026-10-16

The operator confirmed audible siren output from the single camera-3 two-second
strict pulse attempt. However, the operation returned `camera did not confirm the
final siren OFF state` after 19.44 seconds (38.5 MiB peak, no swap). The ON application
response passed; the routine attempted its unconditional OFF cleanup, but this result
does not distinguish an OFF application failure from a failed final state read.
A subsequent read-only check failed at access-node heartbeat. One safety OFF-only
attempt also failed at heartbeat, before sending its OFF command. No activation was
repeated. Audible output is physically confirmed; exact audible duration and final
OFF are not confirmed by the retained evidence. Do not register a complete successful
pulse proof or enable strict rollout on this basis.

The operator explicitly prohibited further audio/siren tests because people are
sleeping. Only visual tests are allowed until that restriction is lifted. Diagnose
the cleanup/session failure offline; do not retry sound tests automatically.

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
