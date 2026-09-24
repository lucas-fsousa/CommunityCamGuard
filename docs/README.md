# Documentation

Docs are split by audience:

## `public/` — for users and integrators

- **[Recordings and playback](public/recordings.md)** — original MP4 vs codecs, native playback,
  fallback/cache limits, downloading and current validation boundaries.
- **[Bluetooth onboarding](public/bluetooth-onboarding.md)** — setup steps and remaining cloud dependency.
- **[API reference](public/api.md)** — every REST endpoint, for building your own UI or scripts.
  The always-current schema is served live at `/api/openapi.json` (Swagger UI at `/api/docs`).

## `internal/` — for contributors

- **[Internal index](internal/README.md)** — ADRs and current implementation/measurement notes.
- **[Native recording playback](internal/recordings-native-playback.md)** — negotiation, fallback,
  HTTP checks and capped real-browser validation; unverified cases are explicit.
- **[Playback lifecycle](internal/recordings-playback-lifecycle.md)** and
  **[measurements](internal/recordings-playback-measurements.md)** — selection cleanup, encoder budget,
  Range delivery, observed latency and scoped logging.
- **[Settings inventory and plan](internal/settings-dashboard-plan.md)** — classification of 42
  options and required authentication/application boundaries; not an implemented settings UI.
- **[Session principal](internal/session-principal.md)** — implemented primary/legacy session
  parsing and management gate, migration policy and rollout status; temporary keys remain pending.
- **[Historical decisions](DECISIONS.md)** — earlier reasoning and progress; later amendments and
  focused implementation notes describe the current behavior.

## Elsewhere in the repo

- **[`../ROADMAP.md`](../ROADMAP.md)** — backlog, priorities, milestones (living doc).
- **[`../CONTRIBUTING.md`](../CONTRIBUTING.md)** — how to build, test and submit changes.
- **[`../README.md`](../README.md)** — project overview and quick start.
