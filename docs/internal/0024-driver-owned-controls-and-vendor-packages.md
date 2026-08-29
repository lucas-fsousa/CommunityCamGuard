# 0024 — Driver-owned controls and vendor packages

**Status:** accepted, P2P verticalization implemented · **Date:** 2026-08-28

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
- The recovered GAT/IoTVideo transport, crypto, authentication, RTSP setup and typed feature
  operations live under `drivers/yoosee/p2p`; there is no application-global `vendor_p2p` package.
  Generic services may depend on driver contracts, while Yoosee-specific onboarding adapters import
  this vertical implementation explicitly.
- P2P errors, allowlists and typed results live in `p2p/contracts.py`, independent from UDP session
  orchestration. `p2p/client.py` retains compatibility reexports while feature adapters migrate to
  the stable contracts module.
- Common IoTVideo frame construction, randomized flags, checksums and mode-1/mode-2 finalization
  live in `p2p/wire.py`, giving protocol codecs a socket-free dependency base.
- The allowlisted GDM property-read codec (`B7`, `B8` and brokered `AA`) lives in
  `p2p/model_protocol.py`; JSON/path parsing no longer shares a module with UDP rendezvous loops.
- Direct rendezvous packet construction (`A4`, `CA`, `CB`) and `A3` peer parsing live in
  `p2p/rendezvous_protocol.py`, separate from the code that owns sockets and retry budgets.
- Access-node discovery, certification, initialization, TermDNS and heartbeat frame codecs live in
  `p2p/access_protocol.py`; their UDP lifecycle, timeout and retry orchestration live in
  `p2p/access_session.py`.
- Shared UDP deadline handling, access-node frame decryption and reliable acknowledgements live in
  `p2p/session_io.py`. Feature modules consume that boundary directly instead of private client
  helpers.
- Direct camera NAT rendezvous and handshake retry orchestration live in
  `p2p/rendezvous_session.py`; packet construction remains isolated in
  `p2p/rendezvous_protocol.py`.
- Allowlisted thing-model read retries, correlation and report handling live in
  `p2p/model_session.py`; JSON and frame parsing remain socket-free in `p2p/model_protocol.py`.
- Durable enrollment selection and the authenticated access-node-to-camera handshake live in
  `p2p/camera_session.py`. Feature modules call this explicit boundary instead of a private helper
  on the compatibility client.
- Encrypted Yoosee account/session persistence lives in `drivers/yoosee/account_store.py`. It keeps
  the existing `vendor_accounts` table for an in-place upgrade, but no longer presents a
  manufacturer-specific repository as a generic `db` module.
- The driver-generated control catalog is authoritative for API/UI gating. The old
  `vendor_controls` response is a temporary compatibility projection of that catalog.
- The canonical HTTP boundary is `/api/cameras/{camera_id}/controls/{control_key}`. It accepts only
  a scalar semantic value and the application service rejects keys or operations absent from that
  camera driver's catalog before transport dispatch. The older `/api/vendor-controls/...` routes
  remain temporarily for client compatibility.
- The bundled dashboard consumes only the authoritative `controls` descriptors and canonical
  camera-control route. Compatibility fields/routes are no longer dependencies of current UI code.
- In-repository drivers remain explicitly registered. Automatic filesystem imports are rejected:
  registration order affects detection and implicit imports make startup and security auditing less
  predictable. Python entry points may be added later if out-of-tree plugins become a real need.
- Registry construction fails fast on duplicate keys, a missing/misplaced generic fallback, or an
  onboarding adapter whose declared `driver_key` differs from its owning driver. A bad plugin can
  no longer silently replace routing entries in the key index.
- A family driver may contain model/firmware profiles. We do not create one application-level
  driver instance per physical camera, nor assume every model of a brand shares one wire contract.

## Consequences

- Adding an implementation of an existing semantic control no longer changes the HTTP router or
  frontend. Unsupported cameras fail through the driver contract and never inherit another
  family's P2P features.
- Vendor transports can keep rich typed internal results while returning a stable public result.
- New semantic control kinds still require an intentional contract/UI addition; this is preferable
  to an unsafe generic command tunnel.
- ADRs 0025–0027 subsequently remove MAC from media, recording and registry identity. The proprietary
  P2P implementation and account persistence are now inside the Yoosee driver. ADR 0028 completes
  the next boundary: generic API/startup code reaches factory provisioning through a driver-owned
  onboarding port rather than importing the Yoosee implementation.
