# 0028 — Semantic API routers and driver-owned onboarding

**Status:** accepted · **Date:** 2026-08-28

## Context

The original dashboard API accumulated authentication, camera CRUD, LAN discovery, live media,
recording playback and Yoosee factory enrollment in one `routes.py`. Besides making unrelated
changes collide, the generic application entry point initialized Yoosee storage and the HTTP layer
imported its account and P2P client directly. Adding another family would therefore require editing
generic API and startup code even when its implementation already fit the camera-driver contract.

## Decision

- HTTP endpoints are grouped into semantic routers (`cameras`, `discovery`, `media`, `recordings`
  and provisioning) while retaining the existing `/api/...` paths and authentication rules.
- Shared camera lookup, capability probing and media/recorder reconciliation are application
  services. Camera JSON shaping is one API presenter instead of being copied across routers.
- Factory enrollment operations that vary by manufacturer use `OnboardingPort`. A driver may
  provide that port; unsupported drivers return none.
- The generic API asks the driver registry for an onboarding provider. It does not import Yoosee
  account persistence, account protocol, inventory probes, direct-route probes or property reads.
- Startup initializes onboarding stores by iterating registered providers, so a new provider does
  not add a vendor import to `main.py`.
- P2P operations cross the port as typed, secret-free DTOs. Raw session tokens, native payloads and
  peer coordinates remain inside the driver package.
- While Yoosee is the sole onboarding provider, omission of a driver key resolves it unambiguously.
  When a second provider is registered, callers must select a driver explicitly; silent selection
  is rejected.

## Consequences

- Camera families remain vertically packaged and can add onboarding without changing generic
  startup or importing their protocols into HTTP code.
- The external API is unchanged; direct imports of old route functions were internal test details
  and now point at their semantic modules.
- Common Wi-Fi selection and HTTP validation remain reusable. Yoosee's protocol recovery adapter
  can move more implementation files beneath its package incrementally without changing the port.
- The driver registry remains explicit and auditable; onboarding is not discovered by arbitrary
  filesystem imports.
