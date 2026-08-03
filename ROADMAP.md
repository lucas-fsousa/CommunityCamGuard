# ROADMAP — Community Cam Guard (CCG)

Living document: backlog, priorities and milestones. Technical detail and rationale live in
`docs/` (ADRs); this file is **what** and **in what order**, not **how**.

Priority: **P0** critical · **P1** high · **P2** medium · **P3** opportunistic.
Status: `todo` · `wip` · `done` · `blocked`.

---

## Milestone M1 — Live video quality (Feature 1)  ⟶ IN PROGRESS

Goal: get the picture close to the vendor app, with a **quality selector** that defaults to the most
the camera + host can sustain. Diagnostic reference: `docs/DECISIONS.md §34`.

| Priority | Item | Status |
|---|---|---|
| P0 | **Target bitrate + GOP** on the transcode (`-b:v/-maxrate/-bufsize/-g`) — was go2rtc defaults only | done |
| P0 | **Quality levels** model (`low`/`medium`/`high`/`max`) mapping source→bitrate (`media/quality.py`) | done |
| P0 | `live_quality` setting (default `max`) + unit tests (`test_quality.py`, `build_config` wiring) | done |
| P1 | **Per-camera quality selector** in the UI — Auto/HD/SD dropdown (client-side/instant) + endpoint exposes quality | done |
| P0 | **HD freezing FIXED** — it was MSE-over-internet (remote viewer). Forcing `mode=webrtc,mse` in the player fixed it (confirmed smooth on both cameras). Diagnosed via `scripts/diagnose_streams.sh` | done |
| P1 | **Control polish** (feedback): quality dropdown, PTZ D-pad, borders on all buttons, taller bar | done |
| P2 | **Hardware acceleration** (`live_hwaccel`: vaapi/cuda/v4l2m2m/…) — plumbing + tests done | done¹ |
| P1 | **Visual validation** of the bitrate levels + hwaccel against the real go2rtc | blocked² |
| P2 | `high` profile/preset on the encoder — go2rtc ships UPX-packed, template not inspectable; validate first | blocked² |
| P2 | Camera-hiccup resilience (transcode EOF→reconnect) — go2rtc timeout/reconnect tuning (secondary factor) | todo |
| P2 | Auto-degradation under CPU pressure (honours `grid_hd_max_cameras` as the host guard) | todo |
| P2 | UI: global `live_quality` (bitrate) control — needs a settings endpoint + go2rtc restart | todo |
| P3 | Measure and document quality vs. the vendor app (bitrate/resolution/latency side by side) | todo |

¹ Gated (default `""` = software, current behaviour); needs a GPU to matter.
² Depends on hardware / a human eye on the real streams.

## Milestone M2 — Architecture & code quality (Feature 2)

| Priority | Item | Status |
|---|---|---|
| P1 | Tooling: `ruff` (focused ruleset) + `mypy` (clean, 31 files) + CI running lint+types+tests | done |
| P1 | `black` configured in pyproject — **applying** a repo-wide format (37 files) is a separate deliberate step | todo |
| P1 | Test coverage ≥ 90% — **65% → 78%** (244 tests). Done: `active_scan.py` 61%, `drivers/base.py` 100%, `rtsp.py` 92%, `routes.py` 88%, `main.py` 76%, `ptz.py`/`device.py`. Left: `ws_discovery` 24%, `recorder.py` 66%, rest of `active_scan` | wip |
| P1 | Formalise the driver layer (Strategy + Factory) — **already done**: `CameraDriver` + ordered registry + `detect`/`for_camera`/`get` + generic fallback | done |
| P1 | Dependency injection for services (go2rtc, recorder, registry) — remove singletons/coupling | todo |
| P2 | Remove scattered per-vendor conditionals; isolate them in drivers/adapters | todo |
| P2 | Split large files by single responsibility (audit `recorder.py`, `routes.py`) | todo |

## Milestone M3 — Documentation & cross-platform (Feature 3)

| Priority | Item | Status |
|---|---|---|
| P1 | `docs/public/` + `docs/internal/` structure + index. **ADRs started** (0001–0010 cover the pillars + control/storage); migrating the remaining `DECISIONS.md` decisions in batches | wip |
| P1 | **API/endpoint docs** — `docs/public/api.md` (reference) + Swagger/ReDoc at `/api/docs`, `/api/redoc`, schema at `/api/openapi.json` | done |
| P2 | README with a documentation index pointing to `docs/`; roadmap de-duplicated; facts updated | done |
| P2 | `CONTRIBUTING.md`: standards (ruff/mypy/pytest), PR flow, no-secrets rule, driver plug-in guide | done |
| P2 | Infra: Dockerfile/compose/.dockerignore reviewed (stale "SKELETON" comment fixed, lean build context, cross-platform framing) | done |
| P2 | Windows/Linux/macOS documented (Docker runs on all; WSL reframed as one Windows option, not a requirement). Left: actually test on native macOS/Windows | wip |

## Out of scope / parallel track

Direct control of **reboot and two-way audio** lives in the vendor's proprietary P2P channel, not
ONVIF (see ADR 0008) — reverse-engineering it is a separate track and does not block the milestones
above.

---

_Convention: when an item is done, mark it `done` and move the technical detail/rationale into an ADR
under `docs/internal/`._
