# Driver-owned PTZ model selection — 2026-09-15

Latest update: Garagem/Quintal access-only P2P enrollment has now been recovered
without rebinding cameras; firmware 40.1.22 is explicitly covered by the driver.
See [existing-camera-p2p-recovery.md](existing-camera-p2p-recovery.md). The missing
enrollment/subscription concerns below describe the earlier investigation; a
subscription token is not required for brokered PTZ preparation.

## Follow-up: shared D-pad

The initial model-selection change did not fix the visual split: the frontend still
rendered the old arrows for `ptz_interaction=hold`. Runtime inspection showed only
camera 3 had a durable P2P enrollment; Garagem and Quintal had none, rather than
merely a missing link to an existing enrollment. Their documented MAC/device
associations are retained in ignored RE notes, not guessed from IP/name/order.

`live-cameras.js` now always uses the shared finite click/drag D-pad when the camera
has PTZ capability. Yoosee reports the step interaction regardless of P2P enrollment;
the existing ONVIF `move` implementation already sends a bounded pulse followed by
STOP. Driver transport selection and safe native fallback remain unchanged. No
fake enrollment, camera command or account rebind was performed.

Tests cover shared component selection for step/hold/legacy camera descriptors,
the ONVIF step route without enrollment, and existing click/drag/STOP contracts.
Native P2P for Garagem/Quintal still requires recovery of genuine enrollment
credentials: the current account inventory helper itself requires an enrollment,
and the onboarding bind path requires temporary provisioning material. Merely
knowing a device ID is not sufficient to synthesize its subscription token.

The following sections describe the previous backend model-selection milestone.

User clarification: camera 3 was the authorized physical test unit, not the only
unit allowed to benefit from the implementation. Support decisions belong to the
driver. Homologation of a compatible model/profile must not require repeating a
manual camera-ID activation for every device of that profile.

`ptz_models.py` now contains the driver-owned finite-step/STOP compatibility
catalogue. Its key is product/model/revision/firmware/SDK/hardware, never camera
ID, MAC or friendly name. The initial entry is the tested GW-IPC-AK-AV100.25 profile
(product 6442451494, revision 1, firmware 40.1.14, SDK 16.20.16355, empty hardware).
Version/hardware compatibility beyond this observed profile remains to be mapped;
same brand alone is not a capability grant.

For every P2P-enrolled camera, driver dispatch selects finite-step preparation
without requiring a `yoosee_native_ptz_rollout` row. Preparation reads correlated
product/version identity, chooses the compatible model profile, and intersects
supported directions with the actual camera's current axis evidence. No START is
constructed if identity, model compatibility or the requested axis is unavailable;
existing preflight-only ONVIF fallback applies. The candidate selection is not a
claim that an unprobed model supports native PTZ.

The old exact-unit records remain only as compatibility with internal experimental
callers; enrolled production cameras take the automatic driver path first. No bulk
per-camera enrollment/rollout rows were inserted. No change to the generic driver
contract, frontend brand branching, movement duration, route-cache lifetime or
post-START no-retry invariant. Renewed credentials still must bind the same camera.

Regression evidence: 139 focused PTZ/renewal tests passed, plus Ruff, Mypy and
frontend toast/panel/PTZ contracts. Tests demonstrate two different device IDs
using the same model profile without per-unit opt-in, automatic dispatch for an
enrolled device, and native refusal for an unknown profile. No physical movement
or camera feature write was used in this change.
