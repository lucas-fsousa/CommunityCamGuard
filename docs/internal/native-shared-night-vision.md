# Three-state night mode on the native account — 2026-10-30

The existing Yoosee night-mode adapter can now reuse the active native account,
like volume and orientation. The generic API/catalogue and exact-device driver
proofs are unchanged. No new control or model-wide support is inferred.

`shared_night_vision.py` admits only automatic/daytime/night, mapping to0/1/2.
`NightVisionWriteSpec` joins the closed typed scalar union and existing one-slot
inbox/shared sequence namespace. Its D2 builder changes only the already-mapped
`ProWritable.videoParm.setVal.nightViewMode`, never multiFlip, the complete video
root or the unrelated V2 bitfield. There is no arbitrary path or JSON write API.

Shared preflight reads the legacy scalar and rejects missing/unknown/V2-only
states. An unchanged request performs no write. A change sends one D2, requires
the current peer/session/generation/message's D3, then exact bounded readback.
Delayed state is polled without replaying D2. Losing the owner, a rejected D3 or
unconfirmed readback never triggers another login or legacy fallback. Outside
native ownership, the existing standalone adapter remains available as before.

Tests use synthetic replies and the actual socket-free worker/inbox. Coverage
includes all modes, no-op, adjacent-field preservation, peer/session/generation/
message/expiry rejection, readback failure and no duplicate send/fallback.
152 focused adapter/API/worker tests passed under192MiB/no swap/50% CPU
(113.8MiB peak); Mypy378 files and Ruff passed.
No physical mode change or visual acceptance was performed in this session.
IR illumination still depends on ambient light/firmware and is not proven by a
successful D3/model response. This is shared-account implementation, not a claim
that daylight IR behavior changed or has been physically revalidated.

The first full CI run (`40996516375`) found five existing driver-contract tests:
invalid inputs escaped as ValueError instead of ControlOperationError. The adapter
now preserves the prior public error contract before preflight and additional
native-owner tests assert no read/write for those inputs. The133 focused driver,
adapter, API and worker regressions pass under192MiB/no swap (110.2MiB peak).

The initial deployed integration (`eeea561`, `b-cf593ab9f6ca`) resumed camera3 at
native revision59, kept both other cameras legacy0 and reported3online/recording.
Passive H264/AAC MSE received1355/551201/598946byte fragments and all original
recordings were nonempty/recent (2.9/1.5/1.3s). Public/local builds matched, no OOM
or unexpected app restarts. No night-mode write or sound/light/motion test was
sent. The error-contract follow-up is required before calling its full CI green.

Follow-up `575ed76` is deployed as `b-3422b2883cd9`. Full CI40996808966 passed
5586 tests (one existing skip) plus all Node contracts, Ruff and Mypy. Image
40996808948 passed. Clean startup resumed camera3 native revision62; all three
cameras report online/recording, other cameras remain legacy0. App reports no
OOM and zero restarts. The preceding failed CI remains recorded above rather
than being counted as successful. No physical control was actuated for either
deployment; the pending visual/IR observation is unchanged.
