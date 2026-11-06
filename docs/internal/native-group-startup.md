# Native group startup — 2026-11-06

## Delivered path

Ordinary server startup builds a driver-neutral plan from cameras whose persisted
source mode is already `native`. Legacy RTSP cameras are never passed to the
planner, migrated, opened or granted new capabilities by this operation.
`CameraDriver.native_media_offers` defaults to existing individual offers; a
driver may supply a coordinated group without adding vendor knowledge to core.

Yoosee plans up to three explicitly reviewed units from one enrolled account.
Each member needs its own current identity/media proof and, when recording,
original-A/V proof. PTZ grants remain per-device/per-direction. Missing proof,
duplicate members, multiple accounts, excessive size or absent server ownership
reject the group without logging in. Same brand/model alone is not proof.

Every offer captures the SAME cohort, with independent per-camera resource
domains/leases/consumers. The first admitted run starts the server account; the
others reuse its credentials, worker and authenticated inventory. There is no
login per source/viewer/PTZ gesture. Camera-local media detach does not stop
healthy siblings. The last source intent requires account retirement too.

Driver-declared restart-domain aliases allow exact-camera clean checkpoints to
move from the previous singleton domain to per-camera domains and back. Core
still checks camera ID, current driver evidence, policy generation and the
original one-use checkpoint. Arbitrary domains are not accepted by default.

## Configuration and operation

`NATIVE_SOURCE_CAPACITY` bounds source workers/consumers (1–32, default1).
Use3 for the current three-camera group, budgeting CPU/RAM for each encoder.
It is server-only and requires app recreation; not a remote capability override.
`NATIVE_RESUME_ON_START=true` resumes only eligible existing native intentions.
Increasing capacity does NOT switch a legacy camera to native. First migration
continues to use the existing explicit operator workflow and driver review.

Cohort membership is fixed for a startup plan. Changes are applied on app restart,
not by hot-adding devices to an authenticated SDK inventory. The existing host
installation workflow retires the old containers before its one-camera bootstrap;
normal startup then plans all enrolled native intentions together. No new
camera reset/reboot, sound/light/motor test or firmware change is necessary.

## Integrated proof, not isolated helpers

`test_native_group_startup` uses actual profile/enrollment persistence, Yoosee
driver plan, startup service, runtime, native sources, account lifecycle/worker and
per-device leases. Only the encrypted broker socket, TCP media and decoder are
synthetic. Three native sources consume old singleton-domain checkpoints and
produce three active sources using ONE SDK scope/login/socket. Stopping one
joins its source while two continue; final shutdown closes all three channels,
all consumers and the account. A fourth legacy camera is excluded from planning.

Negative tests cover missing proof, cross-account membership, duplicates, missing
server owner, lazy construction, plan revalidation, singleton-domain compatibility
and bounded capacity. The combined regression passed92tests (180.1MiB peak) and
a settings/group batch passed26 (160.1MiB), with192MiB/CPU50% scopes. Mypy passed
420modules and Ruff passed. These are not claims of three real native cameras;
only camera3 is authorized for current live experiments. The other two stay RTSP.
