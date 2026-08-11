# 0018 — Semantic native ES modules with a thin orchestration entrypoint

**Status:** accepted · **Date:** 2026-08-11

## Context

The dashboard grew from a small script into a 1,040-line `frontend/app.js`. Authentication, API
access, live-player recovery, camera controls, factory provisioning and recordings shared one file.
That made unrelated changes collide and made it difficult to identify which state belonged to each
feature. Adopting a framework or bundler would add a toolchain the project does not otherwise need.

## Decision

Keep native browser JavaScript and split the dashboard by responsibility:

- `modules/core.js`: DOM primitives, API access and shared state;
- `modules/live-cameras.js`: live players, PTZ/zoom/quality controls and freeze recovery;
- `modules/camera-management.js`: discovery, existing-camera management and factory provisioning;
- `modules/recordings.js`: recording filters, pagination and playback;
- `i18n.js`: localisation exports; and
- `app.js`: authentication, navigation, timers and orchestration only.

Small handler injection points join the domains without circular imports. `boot.js` installs an
import map whose URLs all carry the content-derived build identity from ADR 0015, then imports the
player and entrypoint. There is still no compilation, package manager or generated bundle.

## Consequences

- Feature changes have an explicit home and the entrypoint remains short enough to audit quickly.
- The module graph preserves automatic cache busting, including child modules.
- Modern import-map support is required; the dashboard already targets current evergreen browsers.
- Cross-module coordination is explicit through imports and injected handlers rather than globals.
