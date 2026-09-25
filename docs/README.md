# Documentation

Docs are split by audience:

## `public/` — for users and integrators

- **[Recordings and playback](public/recordings.md)** — original MP4 vs codecs, native playback,
  fallback/cache limits, downloading and current validation boundaries.
- **[Bluetooth onboarding](public/bluetooth-onboarding.md)** — setup steps and remaining cloud dependency.
- **[Dashboard settings](public/settings.md)** — primary-only Auto/cache controls, defaults and conflicts.
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
  options and required authentication/application boundaries beyond the two editable fields.
- **[Session principal](internal/session-principal.md)** — implemented primary/legacy session
  parsing and management gate, migration policy and rollout status; temporary keys remain pending.
- **[Runtime settings](internal/runtime-settings.md)** — two-field revisioned persistence/API,
  application semantics, authorization, UI contracts and deployment checkpoint.
- **[Login pacing](internal/login-abuse-protection.md)** — bounded login attempts, proxy limitations and pending security gates.
- **[Temporary live media](internal/temporary-live-media.md)** — restricted MSE bridge and revocation; not deployed.
- **[Temporary keys](internal/temporary-access-keys.md)** — lifecycle and staged management API;
  deployment, session/channel invalidation, temporary login and UI remain pending.
- **[Open-session channels](internal/session-channels.md)** — tested socket expiry/error
  checks, current MSE transport and remaining authorization/activation boundaries.
- **[Staged temporary sessions](internal/temporary-sessions.md)** — signed key linkage,
  fresh expiry/revocation checks and transitional permissions; no temporary login yet.
- **[Dashboard session watch](internal/dashboard-session-watch.md)** — validity polling,
  stale-response rejection and audio/player cleanup; browser validation limits.
- **[Revocable recordings](internal/recording-session-delivery.md)** — in-flight temporary
  delivery cancellation with Range/seek support; deployment/end-to-end checks pending.
- **[Historical decisions](DECISIONS.md)** — earlier reasoning and progress; later amendments and
  focused implementation notes describe the current behavior.

## Elsewhere in the repo

- **[`../ROADMAP.md`](../ROADMAP.md)** — backlog, priorities, milestones (living doc).
- **[`../CONTRIBUTING.md`](../CONTRIBUTING.md)** — how to build, test and submit changes.
- **[`../README.md`](../README.md)** — project overview and quick start.
