# Siren capability migration

## Audio denial classification, 2026-10-20

The generic audio service previously converted every false support predicate into
Unsupported before consulting the driver. Thus expired evidence for an already
homologated audio operation was reported as501, despite being unavailable rather
than proven unsupported. Message and stream dispatch now call the existing generic
`unavailable_control` explanation hook; its default remains Unsupported.

Yoosee explains an explicitly selected operation with an exact-unit/identity,
nonduplicate writable route proof as ControlNotReady (public409). Unselected,
missing or read-only proofs remain Unsupported (501). Explanation performs local
metadata reads only, never credential inspection, camera I/O, refresh or replay.
It does not authorize dispatch; the service still rejects even if a faulty hook
returns normally. Generic services contain no Yoosee branching.

Sixty focused service/API/policy/availability tests passed (134.4MiB/no swap), Ruff
clean. The persisted expiry lifecycle test now exercises actual generic service
dispatch for both audio modes, covering exact expiry, renewal, failed collection and
firmware change without additional transport calls. Public projections409/501 are
tested separately. No frontend redesign, new audio grant, real camera action,
profile mutation or deployment in this step.

## Persisted lifecycle acceptance, 2026-10-19

Both recorded-message and streaming paths now have an integrated test using actual
temporary SQLite profile, rollout and snapshot stores (no production DB). A virtual
clock reaches the exact expiry boundary: support becomes false and send is denied
without additional synthetic transport calls. Publishing fresh matching identity
restores dispatch. Starting collection invalidates prior evidence even if collection
never finishes; publishing another firmware does not inherit the old operation proof.
The refresh scheduler is replaced by a call recorder, so no worker or camera I/O runs.

Thirty focused lifecycle/route/snapshot-store tests passed (130.4MiB/no swap), Ruff
clean. This tests the existing runtime policy without changing its behaviour.
Separate read-only production catalogue checks confirmed camera3's two audio flags
and seven static controls remain available. No audio, real expiry invalidation,
profile mutation or rebuild. Physical endurance/full-duplex remain unvalidated.

## Camera3 audio rollout completed, 2026-10-19

After CI39252686418 passed, the historical RTSP physical acceptance in
[ADR0008](0008-reboot-and-two-way-audio-live-in-vendor-p2p.md#2026-09-01-amendment--proprietary-rtsp-talkback-recovered)
was bound to camera3's exact existing identity/firmware using the document SHA-256
and locator. Both audio_message and audio_stream have writable rtsp_pcm proofs;
neither authorizes p2p_amr. Registration preserves all seven prior operations and
their source references; rollout preserves alarm_voice. Fresh exact identity and
enrollment association were checked before import. No other unit was migrated.

Image4449dfe2d2a5 contains83f76ae; build used512MiB/no-extra-swap/half-CPU limits.
Only app was recreated (19:24:43UTC). No go2rtc recreate command was issued; its
observed start time was13:03:15UTC on October19, not the older October14 timestamp
from prior deployments. Both services reported no OOM and zero restart counters.

Import/post-registration route assertions passed63.6MiB/no swap. Read-only canonical
HTTP catalogue confirmed audio_messages=true, audio_streams=true and all seven
static controls (48MiB/no swap). Health/status/filesystem check confirmed all three
units online/recording, newest nonemptyMP4 ages4/1/4seconds (42.5MiB/no swap).
No media requests or audio/camera actions were sent; physical audio acceptance is
reused from the documented historical tests, not claimed as a new live test.

Remaining: separate proof/migration for other units and any P2P audio route,
physical endurance/full-duplex checks when sound is permitted. Do not rerun the
one-time local importer; its precondition deliberately expects the old seven-operation
profile and refuses after successful migration.

## Audio migration wiring, 2026-10-19

`audio_capabilities.py` isolates the route policy from transport. Existing durable
operation profiles can represent `audio_message` and `audio_stream` independently,
with explicit `rtsp_pcm` and `p2p_amr` route options. Profile registration still
requires a source digest/locator; no proofs are inferred from connection material.

Explicitly selecting either audio operation in a unit's rollout moves both audio
surfaces out of legacy authorization. An unselected/unproven operation fails closed.
The policy requires exact enrollment association, current identity snapshot, matching
profile/unit/firmware and writable route proof. Snapshot freshness is an identity
prerequisite, not audio certification; the separate operation proof supplies that.
Missing/expired snapshots request the existing bounded, read-only refresh worker.

Both support predicates and actual send paths consult this policy. Rejection occurs
before consuming microphone chunks. RTSP remains preferred only when proven and
locally addressable; transport failure never replays audio through an unproven P2P
fallback. PCM framing/pacing and existing unselected units remain unchanged.

Validation:36 route/adapter/backchannel tests plus47 driver/architecture/intercom-API
tests, at most111.8MiB/no swap across runs, Ruff clean. Includes real temporary SQLite
profile/rollout interaction, firmware replacement, duplicate/read-only/missing proofs,
unknown routes, expired identity and separate streaming permission. No real camera
traffic, sound, profile import, rollout activation or deployment occurred.

Remaining: audit and bind the existing physical recorded-message/hold-to-speak proofs
to each exact unit's identity and RTSP route, preserve unrelated operation sources,
then explicitly activate and verify catalogue/dispatch on camera3. Do not register
P2P from RTSP success. Until activation, existing production audio gates remain legacy.

## Audio preflight hardening, 2026-10-19

The RTSP backchannel previously checked whether the Public header contained the
substring USER_CMD_SET. Synthetic replies announcing USER_CMD_SET_V2,
X_USER_CMD_SET or USER_CMD_SET PLAY therefore incorrectly opened the media path.
Three regression cases reproduced this before correction. The driver now splits
the comma-delimited method list and requires an exact normalized token, preserving
existing whitespace/case tolerance. Unknown or empty lists fail before OPEN/media;
the probe socket is closed. Positive exact-method and whitespace tests pass.

Sixteen focused backchannel/intercom/stream tests passed (87MiB peak, no swap),
along with Ruff. PCM framing, prebuffering, pacing and watchdog timing are unchanged.
No real camera sockets, audio, deployment or capability-profile changes occurred.
This is a preflight correctness fix, not complete route-specific capability migration
or a replacement for per-unit provenance. Deployment completed as recorded below.

Additional silent watchdog coverage uses a virtual clock and synthetic RTSP peer:
a ten-second message sends1000 native packets, refreshes OPEN at0/4/8 seconds,
drains until10s and closes. A rejected refresh aborts further packets but still
sends CLOSE and closes sockets. All11 backchannel tests pass (82.2MiB,no swap).
No real waiting/audio/camera contact; physical repeated-session and full-duplex
acceptance remain pending. This adds tests only, with no watchdog timing change.

### Deployment verification

After successful CI39244025452 and39244133194, image fc76427de9e2 was built with
512MiB/no-extra-swap/50%-CPU build-step limits. Only app was recreated at
2026-10-19T12:10:43UTC. The running backchannel source SHA-256 matches the workspace
(`d5eb23a10074a1f380e19fd33c5e04386e766cdef2d941898e9b4b2d5c0ff0f7`).
go2rtc retained its October14 start time. Both containers reported no OOM and zero
restart-counter increments (app recreation is separate from that counter).

Read-only authenticated status and filesystem acceptance showed all three cameras
online/recording, each with a nonempty MP4 modified2 seconds earlier. Probe peak
59.9MiB/no swap,4.360s. No media requests, camera actions or sound tests; this verifies
deployment/recording recovery, not new physical audio acceptance.

Status (2026-10-18): camera-3 strict two-second pulse physically confirmed (including
stop), following the later authorized retest below. Runtime wiring deployed and exact-unit
two-second profile/rollout activated; fresh HTTP catalogue accepted. No sound replay.

## Deployed exact-unit migration, 2026-10-18

Code82b9179 passed full CI39133559433 in55s. Image0c1a0dd78c13 built with512MiB,
no additional swap and half a CPU; only app recreated, go2rtc retained. Camera3
profile registration preserved all six previous operations and their source references,
added only writable `siren_pulse` option `2`, and retained dynamic `alarm_voice`
rollout. Provenance references the SHA-256 of the existing
[physically confirmed results](camera3-controls-followup-2026-10-16.md#results).
No other camera profile was changed. Registration alone does not certify a device.

The old snapshot was invalidated explicitly. A bounded canonical HTTP catalogue
check triggered production-owned collection and confirmed fresh evidence plus all
seven static controls; siren options were exactly `["2"]`. Night/volume options
remained unchanged. Check5.588s,46.9MiB/no swap. Health OK, app/go2rtc running,
neither reports OOM. Zero actuation/media commands were sent by these procedures.
The existing physical pulse proof is reused; a real dashboard-originated pulse was
not repeated, so that specific end-to-end actuation check remains separate.

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
seconds. Tests use synthetic data, never real sound. The later deployment/registration
and read-only HTTP acceptance are recorded above; unit tests do not prove audibility.

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
