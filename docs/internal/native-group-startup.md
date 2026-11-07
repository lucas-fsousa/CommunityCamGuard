# Native group startup — 2026-11-06

## Delivered path

Ordinary server startup builds a driver-neutral plan from cameras whose persisted
source mode is already `native`. Legacy RTSP cameras are never passed to the
planner, migrated, opened or granted new capabilities by this operation.
`CameraDriver.native_media_offers` defaults to existing individual offers; a
driver may supply a coordinated group without adding vendor knowledge to core.

Yoosee plans the explicitly reviewed units from one enrolled account, with no
hardcoded camera-count ceiling.
Each member needs its own current identity/media proof and, when recording,
original-A/V proof. PTZ grants remain per-device/per-direction. Missing proof,
duplicate members, multiple accounts or absent server ownership
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

There is no camera-count configuration or imposed three/32-camera maximum.
The server allocates source workers on demand for admitted registered cameras.
Queues/frames remain per-camera bounded; authentication, duplicate-owner rejection,
cleanup receipts and actual OS resource constraints remain enforced.
`NATIVE_RESUME_ON_START=true` resumes only eligible existing native intentions.
Adding cameras does NOT switch a legacy camera to native. First migration
continues to use the existing explicit operator workflow and driver review.

Cohort membership is fixed for a startup plan. Changes are applied on app restart,
not by hot-adding devices to an authenticated SDK inventory. The existing host
installation workflow retires the old containers before its one-camera bootstrap;
normal startup then plans all enrolled native intentions together. No new
camera reset/reboot, sound/light/motor test or firmware change is necessary.

Reinstallation preserves an existing stronger operation proof and its provenance
when the freshly collected exact identity still matches and the old proof covers
the installer's baseline with the same read/write permissions. In particular,
reinstalling an accepted LAN UDP unit must not silently replace hevc+lan_udp with
hevc-only and force it back to TCP. Firmware/model/SDK changes cannot inherit
that old transport review; they receive only the explicitly reviewed installation
baseline. This correction is in the host installation path and does not require
restarting a healthy running server to take effect on the next installation.

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
and dynamic worker admission. A five-source variant additionally forces TCP EOF
without an inner close receipt, verifies recovery with the SAME login and shuts
down cleanly. This reproduces the shared-path regression: valid physical close
plus outer broker release must not be marked uncertain solely for missing inner
ACK. Known transient protocol codes are now preserved from worker to lease/retry;
unclassified errors or missing physical/outer release still cannot retry.
The combined earlier regression passed92tests (180.1MiB peak) and
a settings/group batch passed26 (160.1MiB), with192MiB/CPU50% scopes. Mypy passed
420modules and Ruff passed. These are not claims of three real native cameras;
only camera3 is authorized for current live experiments. The other two stay RTSP.
One larger local batch was killed by its192MiB cgroup after reaching that ceiling;
it was rerun in smaller batches, not by raising WSL memory or running the full suite locally.
