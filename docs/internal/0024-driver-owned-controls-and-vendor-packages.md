# 0024 — Driver-owned controls and vendor packages

**Status:** accepted · **Date:** 2026-08-28

## Context

ADR 0001 introduced a useful RTSP/ONVIF driver registry, but proprietary Yoosee work later grew in
top-level `provisioning` and `vendor_p2p` packages. The HTTP router imported those implementations
directly and advertised controls from the presence of P2P material rather than from the selected
driver. A second proprietary family would therefore require changes across API, frontend and core.

The persisted public `camera_id` already lets the application select a camera without exposing a
native identifier. The same indirection must select behavior: an enrollment, open port or vendor
string alone must never grant another driver's controls.

## Decision

- Every controllable operation is represented by a semantic, vendor-neutral descriptor and result.
  HTTP accepts values such as `white_light=true`, never an opcode, thing-model path or raw payload.
- An application service resolves `camera_id -> camera -> driver`; only that driver may describe,
  read or write controls for the camera.
- Camera-family code is organized as a package under `drivers/<family>/`. The Yoosee package owns
  the adapter from semantic controls to its encrypted P2P enrollment and protocol operations.
- The driver-generated control catalog is authoritative for API/UI gating. The old
  `vendor_controls` response is a temporary compatibility projection of that catalog.
- In-repository drivers remain explicitly registered. Automatic filesystem imports are rejected:
  registration order affects detection and implicit imports make startup and security auditing less
  predictable. Python entry points may be added later if out-of-tree plugins become a real need.
- A family driver may contain model/firmware profiles. We do not create one application-level
  driver instance per physical camera, nor assume every model of a brand shares one wire contract.

## Consequences

- Adding an implementation of an existing semantic control no longer changes the HTTP router or
  frontend. Unsupported cameras fail through the driver contract and never inherit another
  family's P2P features.
- Vendor transports can keep rich typed internal results while returning a stable public result.
- New semantic control kinds still require an intentional contract/UI addition; this is preferable
  to an unsafe generic command tunnel.
- This is the first migration slice. Factory provisioning, vendor account storage, native identity,
  media source selection and recordings still have transitional Yoosee/MAC coupling and will move
  behind driver/application boundaries in later commits.
