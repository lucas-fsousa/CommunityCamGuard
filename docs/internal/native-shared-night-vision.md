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
