# ROADMAP — Community Cam Guard (CCG)

Living document: backlog, priorities and milestones. Technical detail and rationale live in
`docs/` (ADRs); this file is **what** and **in what order**, not **how**.

Priority: **P0** critical · **P1** high · **P2** medium · **P3** opportunistic.
Status: `todo` · `wip` · `done` · `blocked`.

---

## Milestone M1 — Live video quality (Feature 1)  ⟶ CORE DONE

Goal: get the picture close to the vendor app, with a **quality selector** that defaults to the
camera's maximum resolution; Auto/SD are explicit user choices for weaker hosts. Diagnostic
reference: `docs/DECISIONS.md §34`. Core delivered
(quality levels, per-camera selector, both freezing bugs fixed); the remaining rows are P2/P3 polish
or wait on hardware/a human eye.

| Priority | Item | Status |
|---|---|---|
| P0 | **Target bitrate + GOP** on the transcode (`-b:v/-maxrate/-bufsize/-g`) — was go2rtc defaults only | done |
| P0 | **Quality levels** model (`low`/`medium`/`high`/`max`) mapping source→bitrate (`media/quality.py`) | done |
| P0 | `live_quality` setting (default `max`) + unit tests (`test_quality.py`, `build_config` wiring) | done |
| P1 | **Per-camera quality selector** in the UI — Auto/HD/SD dropdown (client-side/instant) + endpoint exposes quality | done |
| P0 | **HD transport hardening** — prefer WebRTC; bounded/recoverable MSE queue; no 0.1× live playback fallback | done |
| P1 | **Control polish** (feedback): quality dropdown, PTZ D-pad, borders on all buttons, taller bar | done |
| P0 | **Single camera connection + local fan-out**: recording and preloaded H.264 share one RTSP producer; SD is downscaled locally; runtime verified one port-554 session/camera | done |
| P0 | **Frozen-player auto-recovery**: hybrid watchdog distinguishes client stall from local producer stall; only the latter cycles that camera's local preload | done³ |
| — | **Invariant:** recording always uses the base (main) feed at full quality (`-c:v copy`), decoupled from the live quality selector — guard tests lock it | done |
| P2 | **Hardware acceleration** (`live_hwaccel`: vaapi/cuda/v4l2m2m/…) — plumbing + tests done | done¹ |
| P1 | **Visual validation** of the bitrate levels + hwaccel against the real go2rtc | blocked² |
| P2 | `high` profile/preset on the encoder — go2rtc ships UPX-packed, template not inspectable; validate first | blocked² |
| P1 | Camera-hiccup resilience (transcode EOF→reconnect) — visible dead producers are detected and recreated by the hybrid watchdog; server-only retry tuning remains | partial |
| P2 | Auto-degradation under CPU pressure (honours `grid_hd_max_cameras` as the host guard) | todo |
| P2 | UI: global `live_quality` (bitrate) control — needs a settings endpoint + go2rtc restart | todo |
| P3 | Measure and document quality vs. the vendor app (bitrate/resolution/latency side by side) | todo |

¹ Gated (default `""` = software, current behaviour); needs a GPU to matter.
² Depends on hardware / a human eye on the real streams.
³ Applied; needs the user to confirm a real freeze now auto-recovers instead of needing a manual reload.

## Milestone M2 — Architecture & code quality (Feature 2)

| Priority | Item | Status |
|---|---|---|
| P1 | Tooling: `ruff` (focused ruleset) + `mypy` (clean, 31 files) + CI running lint+types+tests | done |
| P1 | `black` — config kept in pyproject, but **deliberately not applied**: a repo-wide format is ~887 lines of pure churn that undoes the author's intentional compact style (which the `ruff` ruleset is configured to allow). Lint/format bar is met by ruff+mypy. Available for anyone who wants it. | done |
| P1 | Test coverage ≥ 90% — **REACHED: 65% → 91%** (302 tests). Every module ≥ 89% (drivers/device/media 100%, storage 97%, recorder 93%, main/rtsp 92%, playback 91%, ws_discovery/routes 88–89%). Only scattered error-branch lines remain uncovered | done |
| P1 | Formalise the driver layer (Strategy + Factory) — **already done**: `CameraDriver` + ordered registry + `detect`/`for_camera`/`get` + generic fallback | done |
| P1 | Dependency injection for services — **already in place**: created in the lifespan, injected via `app.state`, no global service singletons; clean layer direction (registry never imports recording) | done |
| P2 | Per-vendor conditionals isolated — **already done**: audit found **zero** vendor `if`-branches outside `drivers/`; all family logic lives in drivers | done |
| P2 | Split large files / avoid God classes — **audited, OK**: largest is `recorder.py` (439 lines, one cohesive class); `routes.py` is 15 flat handlers; no God class. Optional future polish: split routes into per-domain sub-routers | done |

## Milestone M3 — Documentation & cross-platform (Feature 3)

| Priority | Item | Status |
|---|---|---|
| P1 | `docs/public/` + `docs/internal/` structure + index. **ADRs 0001–0014** cover every load-bearing decision (pillars, control, storage, audio, zoom, playback, i18n); `DECISIONS.md` kept as the historical journal by design (status/UX/bugfix notes aren't ADRs) | done |
| P1 | **API/endpoint docs** — `docs/public/api.md` (reference) + Swagger/ReDoc at `/api/docs`, `/api/redoc`, schema at `/api/openapi.json` | done |
| P2 | README with a documentation index pointing to `docs/`; roadmap de-duplicated; facts updated | done |
| P2 | `CONTRIBUTING.md`: standards (ruff/mypy/pytest), PR flow, no-secrets rule, driver plug-in guide | done |
| P2 | Infra: Dockerfile/compose/.dockerignore reviewed (stale "SKELETON" comment fixed, lean build context, cross-platform framing) | done |
| P2 | Windows/Linux/macOS documented (Docker runs on all; WSL reframed as one Windows option, not a requirement). Left: actually test on native macOS/Windows | wip |
| P2 | Split the oversized `frontend/app.js` into semantic ES modules (`api/auth`, navigation/state, live cameras, camera management/provisioning, recordings), leaving the main file responsible only for boot/orchestration | done |

## Out of scope / parallel track

Direct control of **reboot and two-way audio** lives in the vendor's proprietary P2P channel, not
ONVIF (see ADR 0008) — reverse-engineering it is a separate track and does not block the milestones
above.

### Proprietary-camera capability backlog

| Priority | Item | Status |
|---|---|---|
| P1 | Siren/deterrent ON/OFF over P2P, with bounded activation and guaranteed OFF | done |
| P1 | Map and expose the camera's **selectable siren sound/effect**; the garage unit currently plays a dog-bark effect, proving this is separate from siren ON/OFF | todo |
| P1 | Complete browser microphone → AMR-NB mode 7/8 kHz → camera speaker two-way audio; legacy P2P transport and codec now reproduce the native app, physical intelligibility confirmation pending | wip |
| P1 | Integrate the proven P2P controls into a reusable backend/Docker driver | todo |
| P0 | Keep provisioned cameras operational and controllable without WAN (LAN-only) | todo |
| P1 | Provision a new/reset camera without the vendor Android app or account — localhost-only API/UI and label parser done; recovered QR/SoftAP transport still pending | wip |

---

_Convention: when an item is done, mark it `done` and move the technical detail/rationale into an ADR
under `docs/internal/`._
