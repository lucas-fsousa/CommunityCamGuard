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
| P1 | Driver-independent public camera identity — deterministic opaque `camera_id`, existing-row backfill, stable across MAC re-key, and backend mapping to MAC/P2P/vendor identities; new vendor-control APIs and UI use only this ID. Legacy MAC-addressed APIs remain for incremental migration | done |
| P1 | Dependency injection for services — **already in place**: created in the lifespan, injected via `app.state`, no global service singletons; clean layer direction (registry never imports recording) | done |
| P2 | Per-vendor conditionals isolated — **already done**: audit found **zero** vendor `if`-branches outside `drivers/`; all family logic lives in drivers | done |
| P2 | Split large files / avoid God classes — frontend is split into semantic modules; the Yoosee P2P client was reduced to a compatibility facade while contracts, codecs, sessions and typed feature operations live in bounded modules protected by executable architecture tests | done |

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
| P1 | Siren/deterrent ON/OFF over P2P — physically proven in the RE harness; production now exposes only 2/5/10-second pulses with OFF preflight, single-shot ON, unconditional explicit OFF and final OFF readback. Fresh camera-3 validation through the dashboard remains | wip |
| P1 | Map and expose the camera's **selectable siren sound/effect** — production includes the sanitized type-4 catalogue, bounded C0/C1 fragment/session flow, independent memory-safe QuickLZ level-2 decoder, generic dynamic-option API/UI and guarded selection with fresh-catalogue resolution, preflight and readback. Camera 3 returned all four Portuguese effects and completed `Zumbido 1→Zumbido 2→Zumbido 1` through the canonical API with ACK/error zero/readback and no playback; final audit confirmed the original selection | done |
| P1 | Complete browser microphone → AMR-NB mode 7/8 kHz → camera speaker two-way audio; legacy P2P transport and codec reproduce the native app. Production now has separate, tested MTP/KCP, StreamPipe, meter, AV, intercom-control, bounded OpenCORE encoder/audio sender and device-scoped orchestration layers. Camera 3 completed both a 10/10-frame silent run and a 30/30-frame low-level 740 Hz run; its own microphone returned the matching ~0.65-second band up to 27.6 dB above baseline, proving server-to-speaker playback acoustically while RTSP/recording stayed active. A generic authenticated/LAN-only API accepts at most ten seconds of exact PCM under the per-camera lock; the dashboard now captures via AudioWorklet, resamples in memory, previews locally and sends only after confirmation. The transport sender accepts individually paced frames with per-frame ACK/backpressure, bounded lifetime and no catch-up bursts; the control lifecycle exposes explicit start/frame/close phases with idempotent cleanup. A separate PCM-stream orchestrator owns encoder, direct route, control state and teardown under the existing device renewal lock, behind a driver-neutral iterator contract. The authenticated/LAN-only WebSocket now bridges exact 20 ms frames through a 500 ms queue and blocking worker with idle/session limits and cleanup; no dashboard button invokes it yet. Remaining: push-to-talk UI, browser-level speech intelligibility validation and controlled camera-3 validation | wip |
| P1 | Camera-speaker volume — APK 0/25/50/75/100% positions, raw 0..10 read normalization and D3/readback are implemented in the production driver/API/UI. Camera 3 completed `75→50→75` through the canonical API with ACK/error zero/exact readback; final audit confirmed 75% without playing audio | done |
| P1 | Legacy night vision — automatic/day-color/night-IR values were recovered; the typed production driver/API/UI enforce preflight and exact readback while rejecting unsupported V2 bitfields. Camera 3 production path completed automatic→daytime→automatic with ACK/readback and without selecting IR/night | done |
| P1 | Smart Protection — typed master on/off and weekly schedule are implemented in the production driver/API/UI with preflight/readback and without overwriting adjacent guard settings. Camera 3 production path changed/restored the weekly plan and master with exact readback, ending enabled. Lost list replies have bounded pre-action retransmission; the unrelated direct-route exhaustion was eliminated by keeping controls on the brokered access-node channel. Final dashboard revalidation remains | wip |
| P1 | Integrate proven P2P controls into reusable backend/Docker drivers — reflector, orientation and bounded siren pulse are isolated feature modules with typed LAN-only API/UI surfaces addressed by opaque `camera_id`. Explicit stale-session renewal is bounded to one retry and uses only the encrypted native account; concurrent read/modify/write cycles are serialized per camera. Production controls now use only the brokered access-node route; direct A4/CA/CB rendezvous is reserved for explicit route probes and media. Camera 3 answered six distinct roots in one session at 60–110 ms and then eight fresh canonical HTTP reads consecutively (all verified, 1.8–3.4 s) without allocating a direct link or reproducing the fourth-route failure. Reflector read and reversible orientation are production-validated; bounded siren validation remains | wip |
| P1 | Production P2P bootstrap, direct route lifecycle and allowlisted thing-model reads — encrypted enrollment, list/certification, inventory, heartbeat, A4/A3, CA/CB, native B9 hangup and B7. Four consecutive camera-3 route probes each completed and ACKed teardown, including the former failure threshold | done |
| P1 | Typed image-orientation driver operation — fixed D2 path, normal/180° values only, mandatory B7 preflight and fresh readback; production API/UI and camera-3 reversible normal→inverted→normal validation complete. Protocol readback and independent restream-frame comparison both proved rotation/restoration | done |
| P0 | Keep provisioned cameras operational and controllable without WAN (LAN-only) | todo |
| P1 | Provision a new/reset camera without using the vendor UI — **BLE Wi-Fi flow physically homologated end-to-end** on camera 3 (MTU 256, secure challenge, camera-side Wi-Fi scan/config and `devresult` confirmation). Production now performs account login/refresh and obtains TanKey/bind-token with its own backend implementation; Android, the vendor UI, Frida and capture files are no longer runtime requirements. Vendor WAN/account dependence remains until a LAN-only handshake is recovered; SoftAP remains fallback | wip |
| P0 | Explicit post-Wi-Fi stage matched to the APK and physically homologated: `0x83` is ACK only; race `0x85/confirmKey` with read-only `cloud/netcfg/devresult`, bind only after a separate user action, then start a fresh P2P session. Camera 3 was bound, appeared online in the three-device inventory and answered 37/40 model reads. Production now persists bind material encrypted and proves inventory/heartbeat, direct A4/A3 plus CA/CB rendezvous and bounded B7 reads. Remaining: expose each proven typed write behind an explicit per-device guard | wip |
| P1 | RTSP post-bind contract physically homologated and implemented transactionally in the onboarding modal: fixed `onvifEn`, generated HA1 through `type=3`, authenticated packet proof, encrypted registry commit and service resync. Durable bind can be resumed without rebinding; production implementation still needs a fresh-camera live validation | wip |

---

_Convention: when an item is done, mark it `done` and move the technical detail/rationale into an ADR
under `docs/internal/`._
