# Bounded siren pulse on the native streaming owner

The existing `siren_pulse` driver action now has a shared-native adapter. Public
permissions and exact-unit operation/evidence checks remain unchanged. Only typed
2/5/10second pulses enter; no raw action/path/target or persistent ON is exposed.
No native owner means the existing standalone implementation remains available.
A recovering or mismatched owner never causes fallback/login.

## Owner-driven timing and OFF

Preflight reads exact `Action.expelCtrl` metadata. Unsupported/unknown state or an
already-active siren fails BEFORE ON, without taking over another controller's
alarm. The selected native owner/enrollment must survive admission.

Small separate job/inbox/pump/probe/cleanup modules own the lifecycle. A queued
pulse keeps its original1second admission deadline. Cancellation before ON is
silent. After any possible ON send, timeout/cancel/send failure/shutdown must
drain OFF using the same old socket/session; ON is never replayed. The sole
worker, not an HTTP sleeper, owns the duration. PTZ admission waits during a pulse
rather than overlapping motor cleanup or killing the stream on contention.

Reserve shared sequences for ON, OFF and three independent fixed B7 state reads,
and output capacity for1ON/2identical OFF attempts/3reads/6receipts BEFORE ON.
This uses constant space, not another account/thread/socket. Cleanup-reservation
accounting accepts up to20reserved packets (PTZ6+media2+siren12); total traffic
allowances, media limits and memory caps are unchanged. Normal traffic cannot
consume the OFF/readback reserve. OFF survives namespace retirement on the pinned
old socket, but no new ON can start in a retired generation.

Transport ACK never proves completion. Correlated AD/error0 AND an independent,
fresh, device/sequence-correlated B7 state1 are required for confirmed OFF.
State2 can trigger the second identical OFF, never another ON; bounded state
settling reads remain read-only. Peer/session/generation/message/deadline checks
and fixed codecs stay mandatory. Matching application replies have one ACK owner.
An explicit error cannot be overwritten by a later success.

Unknown OFF poisons admission and retires the account: lifecycle, source-recovery
and media-retirement gates all retain the uncertainty. No automatic reconnect
may erase it or replay the pulse. A bounded cleanup attempt still precedes TCP,
route and socket teardown on graceful shutdown/interruption. No software can
deliver OFF after host power loss/SIGKILL; this is NOT a guarantee against abrupt
loss of power/connectivity or camera firmware faults.

The generic action returns verified success only after the owner finishes and an
additional same-owner final state read confirms OFF. No new capability/model grant
or resource-selection change follows from this integration.

## Verification and current boundary

328 focused tests passed under192MiB/no swap/50%CPU (110.4MiB peak), including
installer regression tests. Mypy400/Ruff passed before deployment. Synthetic
encrypted socket tests cover normal pulse plus media-route/B7/heartbeat traffic,
one ON, exact OFF/state receipts, failed/ambiguous sends, unknown OFF, cancellation,
interruption, shutdown and competing PTZ. The driver adapter and recovery guards
also have positive/negative tests. No real native siren/audio/light/motor action
was executed during that initial synthetic phase; subsequent physical acceptance
is recorded below.

Deployed `4f05980`, image `c5cb6f0e8932`, build `b-d14a329db286`.
Camera3 resumed native/active114. Existing sound options GET200 and the same MSE
socket3→6chunks (1355/491298/245518/522151/247830/492299bytes) passed, revision114
unchanged. All3online/recording, internal diagnostic404/disabled. No pulse was sent.
Image42046396602 passed. Full CI42046396512 exposed three old legacy-only test
fixtures using `object()` as enrollment; the new owner check legitimately needs
camera_id. Those fixtures are corrected without changing strict/compatibility
dispatch expectations or weakening native admission. Follow-up full CI required.

## Physical native pulse accepted

Follow-up CI42046944035 passed5902tests/1existing skip, Mypy400/Ruff and all Node
contracts. Only test fixtures/docs changed after the deployed4f05980 image.
The owner then explicitly authorized ONE2second pulse on camera3. The test first
received real native MSE chunks (1355/504688/266235bytes); ordinary PUT
`siren_pulse=2` returned200, verified/authenticated, application ACK/error0, with
OFF independently confirmed by B7. Transport ACK was absent and correctly reported
false, not waived into a claimed receipt. Source stayed native/active114/available.
The user confirmed the audible test worked. This physically accepts the native
two-second pulse on camera3, not other devices or5/10second grants. No repeat
pulse, light/motor/reset/reboot command was sent. Baseline selected sound remained
Zumbido1 from the separately accepted selection/restoration test.

## Recovery observation follow-up

Camera3 had an obsolete LAN address; a complete bounded MAC-only lookup located
the new address, and only its registry address wasCAS-updated. This does not prove
the cause of the separate transient P2P recovery observed during the checks.
The source did become ready again and delivered the real frames preceding the
accepted pulse; no container restart was used between those recovery checks and
the successful physical test.

Retry diagnostics now preserve fixed connection/worker/protocol labels BEFORE
discarding a retired source attempt. Previously automatic retries could leave no
terminal event, hiding whether recovery was waiting on network opening or media
framing. The same rotating512KiB/two-backup log is reused: no payload, credentials,
network reply, growing history or extra worker. Logging failure cannot stop or
authorize retry; the existing cleanup/evidence classifier remains authoritative.
