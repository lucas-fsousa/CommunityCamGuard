# 0028 — Semantic API routers and driver-owned onboarding

**Status:** accepted · **Date:** 2026-08-28

## Context

The original dashboard API accumulated authentication, camera CRUD, LAN discovery, live media,
recording playback and Yoosee factory enrollment in one `routes.py`. Besides making unrelated
changes collide, the generic application entry point initialized Yoosee storage and the HTTP layer
imported its account and P2P client directly. Adding another family would therefore require editing
generic API and startup code even when its implementation already fit the camera-driver contract.

## Decision

- HTTP endpoints are grouped into semantic routers (`auth`, `cameras`, `discovery`, `media`,
  `recordings`, `storage` and provisioning) while retaining existing `/api/...` paths and guards.
- Provisioning is further split by responsibility: shared request contracts/guards, provider
  account, Wi-Fi/QR, encrypted BLE, privileged P2P and completion. The root status router remains
  intentionally small.
- Shared camera lookup, capability probing and media/recorder reconciliation are application
  services. Camera JSON shaping is one API presenter instead of being copied across routers.
- Factory enrollment operations that vary by manufacturer use `OnboardingPort`. A driver may
  provide that port; unsupported drivers return none.
- The generic API asks the driver registry for an onboarding provider. It does not import Yoosee
  account persistence, account protocol, inventory probes, direct-route probes or property reads.
- Startup initializes onboarding stores by iterating registered providers, so a new provider does
  not add a vendor import to `main.py`.
- The final P2P → RTSP → authenticated-media → registry transaction is also a driver-port
  operation. The HTTP layer receives only a public completion DTO and uses the common runtime
  reconciliation service after the registry commit.
- P2P operations cross the port as typed, secret-free DTOs. Raw session tokens, native payloads and
  peer coordinates remain inside the driver package.
- While Yoosee is the sole onboarding provider, omission of a driver key resolves it unambiguously.
  When a second provider is registered, callers must select a driver explicitly; silent selection
  is rejected.
- Provisioning label/account request contracts carry that optional stable driver key, label
  inspection returns the resolved key, and the status endpoint lists registered providers. This
  makes the explicit-selection rule usable before a second family lands, without breaking the
  current one-provider dashboard.
- The dashboard hides the provider selector when only one onboarding driver exists and propagates
  the selected key through account, label, BLE, privileged and completion requests. Status may
  present the first provider for selector bootstrap; actual onboarding operations never silently
  resolve an ambiguous registry.

## Consequences

- Camera families remain vertically packaged and can add onboarding without changing generic
  startup or importing their protocols into HTTP code.
- The external API is unchanged; direct imports of old route functions were internal test details
  and now point at their semantic modules.
- Large provisioning edits no longer mix cloud-account, radio discovery, BLE secret handling and
  P2P lifecycle code in one Python module.
- Common Wi-Fi selection and HTTP validation remain reusable. Yoosee's protocol recovery adapter
  can move more implementation files beneath its package incrementally without changing the port.
- Label parsing and the recovered Wi-Fi QR wire format/rendering now live under
  `drivers/yoosee`; they are manufacturer behavior, not generic provisioning utilities.
- The encrypted BLE codec, transient attempt store and recovered GATT framing also live in the
  Yoosee driver; the generic API only handles base64 HTTP transport around port DTOs.
- Vendor-cloud BLE material acquisition and post-Wi-Fi privileged binding are contained in the
  same driver. Their cloud dependency is explicit and can later be replaced without changing the
  generic onboarding API.
- LAN location, privileged RTSP activation, authenticated media proof and final registry commit are
  likewise a Yoosee onboarding transaction behind the port. The historical `app/provisioning`
  package now contains only reusable Wi-Fi discovery and signed selection.
- The driver registry remains explicit and auditable; onboarding is not discovered by arbitrary
  filesystem imports.
- No recovered manufacturer protocol remains in the top-level `provisioning` package. New camera
  families should add their own onboarding adapter and codecs under their driver rather than
  extending that generic Wi-Fi utility package.
- Architecture tests enforce both boundaries: generic API/services cannot import the Yoosee
  implementation, and the shared provisioning package cannot silently regain vendor codecs.
