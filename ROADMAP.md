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
| P0 | **Single camera connection + local fan-out**: recording and on-demand H.264 qualities share one RTSP producer; SD reads the base directly, without keeping an unused HD encoder alive. No live preload; same-quality viewers share an encoder; different qualities coexist only while consumed. See ADR 0005 | done |
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
| P1 | Per-camera capability invariant — driver selection assigns responsibility but never grants family-wide features; dashboard controls come only from the exact camera's driver catalogue/support predicates, unknown support fails closed, and architecture tests reject vendor/model/driver UI branching | done |
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
| P2 | Homologate native **Linux ARM64**: build/start both Compose services without x86 emulation; test discovery, recording, live view, controls and two-way audio on real hardware. Current Python/go2rtc image manifests include ARM64, but the complete system has not been tested there. Measure multi-camera CPU/RAM/latency and validate board-specific video acceleration separately; CI build alone is not hardware homologation | todo |
| P2 | Split the oversized `frontend/app.js` into semantic ES modules (`api/auth`, navigation/state, live cameras, camera management/provisioning, recordings), leaving the main file responsible only for boot/orchestration | done |

## Out of scope / parallel track

Proprietary controls remain a parallel track. Standard ONVIF does not expose them on these cameras,
but two-way audio is now recovered through a proprietary **LAN RTSP** backchannel; account-bound
settings and reboot still use the vendor control stack (see ADR 0008).

### Proprietary-camera capability backlog

Capability audit: enrollment-only gates still exist in Yoosee controls and P2P audio fallback.
Offline tri-state evidence and durable per-device/product/firmware storage are tested, including
expiration, rule-revision invalidation and late-response rejection. Runtime collectors, profile
migration and catalogue integration remain pending. See
`docs/internal/yoosee-capability-evidence.md` for the migration sequence.
An explicit four-root, one-session collector now has offline cleanup/timeout tests. It is not
automatically invoked. SDK response construction confirms bit 21 makes B8 offset 0x10 echo
the B7 request sequence. The collector requires device/session/sequence-correlated B8 replies
and excludes unsolicited AA reports; transport ACKs also require matching sequence. Synthetic
encrypted-frame tests pass. Response shape/cache freshness validation and identity normalization
remain prerequisites before observations can drive durable evidence and catalogue gates.
Identity normalization now has offline tests: exact-device product/version roots preserve
product ID/model/revision/firmware/SDK/hardware without family-wide assumptions. Snapshot
validity, writable timestamp validation and atomic identity/evidence integration remain pending;
the normalizer does not yet enable controls or populate the evidence store.
`collect_snapshot` now links collection, exact identity and sanitized enum evidence in
one backend batch. Server receipt time is separate from APK property t (written from
the phone clock on changes). Old t alone does not mean unsupported. Atomic snapshot
persistence, collection ordering/expiry and catalogue/profile migration remain pending.
Atomic snapshot persistence and explicit refresh are now implemented/tested: database-issued
generation before I/O, one-shot conditional whole-snapshot publication, exact identity lookup,
server-time expiry and failed-refresh invalidation. No scheduler/dashboard invocation yet.
Remaining integration: validated operation profiles and deliberate catalogue/audio migration;
property evidence alone is not execution certification or proof of source-cache freshness.
Exact-unit operation/option intersection and `controls.validated_catalog` migration preview
are now tested against persisted snapshots. One DB query resolves all requested features.
The default catalogue/audio gates remain legacy until recorded homologations are bound to
complete identities and a trusted profile source is available; no production allowlist inferred.
Historical proof audit is recorded in `docs/internal/yoosee-homologation-migration.md`.
Orientation (1/3) and Smart Protection master (0/1) now have evidence rules using existing
collector roots; snapshot revision 2 invalidates older results. No production profiles imported:
full per-unit identity/association reconciliation remains open. LAN RTSP audio proof must not
be inherited by P2P fallback, and night mode 2 is not covered by the recorded 0/1 test.
Bounded offline identity auditing now recovers complete historical identity for all three
units from the original August 24 capture. Test-unit association was found read-only in the
local DB; the other two were not returned there. No profile import or DB writes. Current
identity/deployment association must still be verified before switching runtime gates.
Test-unit current identity/deployment association now verified: one bounded four-root read
completed in 2.61 s with correlated B8 success and identity matching historical firmware
40.1.14. No actions, credential renewal, media, DB writes or profile import. Next: bind
historical test-unit operation proofs with provenance and migrate only that exact unit.
Exact test-unit profile is now registered locally with hashed proof references: orientation
normal/inverted, night automatic/daytime, and guard master read/write. Backend profile store
and stored-catalog preview are tested; default dashboard gates remain unchanged. Next: fresh
persisted evidence and stored-preview verification before a deliberate runtime transition.
Camera-3 three-control rollout is now activated after successful fresh snapshot/preview:
orientation, night automatic/daytime and guard master use exact profile + evidence at runtime.
API rejects unadvertised options. Other units/controls/audio remain legacy explicitly.
Refresh is demand-driven, single-worker, five-minute backoff/one-hour evidence validity;
no media/actions/token renewal. Remaining migration needs other feature evidence/proofs.
Runtime rollout deployed and authenticated HTTP-verified on build b-2c117c71d5fa. Only app
recreated; go2rtc untouched. 89 focused tests, Ruff/mypy passed. Build context excludes temp/
and used confirmed 512 MiB/one-CPU build caps. This completes the three-control test-unit
migration; broader controls, audio and other-unit capability gates remain separate backlog.
The test-unit weekly guard schedule is now the fourth migrated control: independently
validated structured plan, shared pure parser, provenance-bound existing homologation and
fresh read/HTTP verification. No schedule or guard setting changed. Build b-d5a1f44f01fe,
96 focused tests passed; other cameras and unmigrated features remain unchanged.
Speaker volume is now the fifth migrated camera-3 control: a successful exact scalar property
read plus existing per-unit readback/restoration proofs allow read and 50/75/100 percent writes.
0/25 remain unhomologated. One fifth allowlisted read was added without changing the 20-second
session budget; snapshot revision 4 invalidates previous evidence. Fresh snapshot, stored preview
and authenticated HTTP catalogue succeeded without playing audio or changing volume. Other units,
white light/siren/dynamic alarm catalogue and audio capability migrations remain separate work.
Manual floodlight is now the sixth migrated camera-3 control. Its type-12 read is correlated
to encrypted session/device/account/request ID; transport receipts alone do not grant support.
One status-only exchange shares the existing five-root collector's session and 20-second budget.
Snapshot revision 5, fresh preview and authenticated HTTP catalogue/read passed using the existing
exact-unit reversible 0→1→0 proof. No lamp action was repeated. Brightness, indicator LEDs and
automatic lighting remain distinct capabilities. Siren/dynamic alarm/audio migrations remain open.
Dynamic alarm-resource migration now has a socket-free proof intersection: exact unit/identity,
complete authenticated bounded catalogue, receipt/expiry validation and individual selection
proofs pinned to semantic key plus native-resource identity digest. Enumeration does not certify
all entries; replaced slots/custom additions cannot inherit permission. Runtime gates are unchanged.
Remaining: original resource-ID proof audit, durable profile provenance, validated observation
collection and shared enumeration/pre-write enforcement. See `docs/internal/yoosee-dynamic-resource-proofs.md`.

Yoosee SD playback handshake invariant: native action `2` is the initiator-side ACCEPT;
action `6` is the subsequent START reply. Session acceptance must observe action `2` and
fails closed on START alone.
Authenticated, device-correlated `E4/PushStreamDistribute` metadata is now collected passively
during rendezvous and MTP opening and carried as an optional platform version; it emits no
additional traffic and remains unknown when the broker does not publish the frame. A bounded
camera-3 route/MTP probe succeeded without AV or commands but produced no E4, proving that this
profile cannot obtain the platform enum from those phases alone.
Native `MessageMgr → iv_send_passthrough_msg → giot_eif_send_passthrough_msg →
iv_gutes_add_send_pkt` analysis now fixes the brokered read carrier byte-for-byte. Production has a
socket-free, command-allowlisted B9 codec for `0/15/16/18`; lifecycle, download, delete and SDK-error
commands are rejected. The full exchange now separately correlates the reliable B9 ACK, the BA peer
receipt and the BuiltIn request ID, while runtime listing remains gated pending one bounded camera-3
validation.
The camera-3 V2 validation delivered the byte-exact request over both the authenticated broker route
and the SDK-equivalent direct mode-1/known-LAN route. Both returned their correlated B9 ACK and BA
receipt within the native ten-second window, but neither returned an application page. Transport is
therefore proven; capability remains hidden until platform selection or actual firmware support is
established without adjacent-version guessing.

Static selector audit (2026-09-09) corrected ascending-order handling: the SDK promotes
every ascending query to at least V3, even below the 49.71-day threshold. Descending daily
queries still use V2 unless authoritative platform metadata selects V4. This fixes a
compatibility defect but does not explain the previous descending-query timeout.

The 2026-09-09 bounded dual-route follow-up sent the same V2 request over LAN and broker:
LAN returned ACK/BA and broker returned ACK, but the instrumented run observed no validated
application envelope or SDK error. Playback receives now allow 32 KiB per datagram (the prior
4 KiB buffer could truncate a native 500-item page); other receivers retain their default.
Correlated BuiltIn error replies are retained as a numeric SDK error and acknowledged, instead
of being swallowed as parser failures. These corrections improve diagnostics and do not certify
SD playback. Next evidence required: the official SDK callback/wire trace for camera 3, or an
authoritative firmware implementation. Further equivalent live retries add no evidence.

| Priority | Item | Status |
|---|---|---|
| P1 | Evolve the Yoosee catalogue into an explicit per-model/firmware capability matrix as additional Yoosee hardware is added. The three current `IPC` units are verified; enrollment or Yoosee detection alone must never enable controls on a newly encountered model without probe/profile evidence | wip |
| P1 | Camera-card recordings — generic opaque-ID/UTC/bounded-page contracts and fail-closed Yoosee `tfInfo` parsing are implemented. Camera 3's real card repeatedly returns APK-defined normal status `1`, a stable card ID and coherent capacity/free values; falling free space proves active onboard recording. The feature stays hidden until a harmless listing probe proves this exact camera/profile. Bounded codecs cover modern V1 command `0`, V2-V4 file command `16`, V2-V4 date command `18`, and internal V3/V4 recording-type command `15`, including V4 fragment assembly. A targeted decompile of Google Play 6.45's real `VSdcardPlaybackVM` proves its gate is only online/AP, non-battery-drain and `tfInfo.stat == 1`; it does not gate by `devFuncCfg`, model or firmware and constructs a V2 daily list with `cameraId=-1`, 500 items and the native ten-second window. The vendor parser now safely accepts that exact 500-item ceiling without expanding the generic 200-item API contract. Native RE pins the exact B9 carrier and both SDK routes: authenticated broker mode 2 plus direct known-LAN mode 1/bit 25. BA is a GAT receipt, not proof of application execution. Camera 3 acknowledged the exact UI-shaped V2 request on both routes but returned no application page even after the prior local five-second clamp was corrected to ten seconds. A camera-3-only, metadata-sanitized Frida hook is ready to capture the official SDK callback/error from one SD-screen visit; until that evidence, the runtime gate and dashboard capability remain closed. Decrypted GAT type `E4` is the only proven platform source and is passively decoded when observed. Card-download commands 19–23 and thumbnail commands 26/27 have socket-free, correlated, bounded codecs without live entrypoints or whole-file buffering. Playback lifecycle has strict stream-begin/EOF decoders, pause/resume/seek builders and platform-specific 1×/2×/4×/8× speed codecs. Native `SDPlaybackPlayer` connection type 2, exact 32-byte A4/AV INIT userdata and `PushStreamDistribute` option `0x4000` are encoded without changing live-view golden frames. Command-25 strategy remains unmapped. The legacy `3→4` parser remains gated by its signed-32-bit-ID manager. Delete (`28/29`) and format remain disabled | wip |
| P1 | Siren/deterrent ON/OFF over P2P — physically proven in the RE harness; production now exposes only 2/5/10-second pulses with OFF preflight, single-shot ON, unconditional explicit OFF and final OFF readback. Fresh camera-3 validation through the dashboard remains | wip |
| P1 | Map and expose the camera's **selectable siren sound/effect** — production includes the sanitized type-4 catalogue, bounded C0/C1 fragment/session flow, independent memory-safe QuickLZ level-2 decoder, generic dynamic-option API/UI and guarded selection with fresh-catalogue resolution, preflight and readback. Camera 3 returned all four Portuguese effects and completed `Zumbido 1→Zumbido 2→Zumbido 1` through the canonical API with ACK/error zero/readback and no playback; final audit confirmed the original selection | done |
| P1 | **Two-way audio from the browser — homologated.** Recorded messages and hold-to-speak use one generic authenticated/LAN-only PCM contract (s16le mono, 16 kHz, 640-byte/20 ms frames). The Yoosee driver prefers the recovered proprietary LAN RTSP backchannel, splits each public frame into two raw-PCM 320-byte/10 ms RTP records and keeps a 100 ms jitter buffer paced by an absolute clock; P2P/AMR remains a driver-internal fallback and downsamples only at that boundary. Physical dashboard tests passed on all three current units across firmware 40.1.14 and 40.1.22, with intelligible audio and cuts reduced to near zero. Session drain/CLOSE, bounded queues, local-only authorization, preview de-duplication and per-camera locking are covered by the full test suite | done |
| P2 | Intercom endurance/polish — observe repeated maximum-duration sessions, `OPEN` refresh across the four-second watchdog boundary, simultaneous listen/talk behaviour and expose content-free underflow/latency diagnostics before claiming full-duplex/long-running operation | wip |
| P1 | Add two-way-audio adapters for other brands behind the same canonical PCM contract; codec, packetization, pacing and capability detection must remain driver-owned | todo |
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
