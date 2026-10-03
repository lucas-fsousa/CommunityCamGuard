# ROADMAP — Community Cam Guard (CCG)

User priority override (2026-10-03): camera-card/SD work is the **last priority**.
Do not resume its enum/listing investigation while native live-streaming work is
available. Current focus: native relay/platform provenance and safe HD preparation.

Native relay follow-up: mapped the actual ready-path quality timer to bounded
telemetry, not resolution selection or the disconnected score helper. Added an
offline generation recheck for already-parsed queued work; retirement/replacement
tests pass. No relay sender, camera traffic or native-HD capability enabled.
Relay address follow-up: SDK constructor now proves separate IPv4/IPv6 sockaddr
storage and IPv4 TCP/UDP advertisement bits. Bounded, socket-free E4 descriptor
parser added; it does not pair families, select routes or authorize connections.
IPv6 transport-bit semantics, response authentication and remote lifetime remain
gates. Follow-up mapped terminal modes 1/2/3 to IPv4/IPv6/both-family observations
in the list-response handler, linking mode 2 to the previously observed IPv6
callback mismatch. This does not prove runtime reachability or camera support.
SDK scanning must use small instruction blocks: one whole-section scan was
terminated at its isolated 256 MiB cap; the 1 KiB-block replacement used 26.6 MiB.
SD remains last priority.
Keepalive follow-up: outbound type-4 44-byte frame is mapped and encoded offline
with strict integer bounds. Incoming type 4 treats the same offset as user ID,
not the sent tick; direction-specific semantics prevent inventing an echo ACK.
The SDK's >8s removal helper only frees local peer entries and has no proven
scheduler in the inspected direct call graph. It is not remote session expiry.
145 focused relay tests passed; no keepalive timer/sender or live probe enabled.
See [keepalive evidence](docs/internal/yoosee-push-keepalive.md).

2026-10-03 remote-login fix: reproduced HTTPS/browser 403 before authentication
with an unset public origin behind TLS termination. Added opt-in direct-local
access alongside a pinned public HTTPS origin, retaining exact-origin checks,
host-only cookies, temporary permissions and revocation. Synthetic tests pass;
deployed as `b-b93ca4beefe6` after CI: HTTPS/local origin checks pass and all three
recorders resumed growing. Actual user-browser delegated login remains to confirm.
Also corrected the
server-only inventory classification omitted for the platform-only RE setting.

Living document: backlog, priorities and milestones. Technical detail and rationale live in
`docs/` (ADRs); this file is **what** and **in what order**, not **how**.

2026-10-01 priority correction: defer further server-recordings playback polish
unless a concrete user-reported failure requires it. Resume the existing Yoosee
native-media/platform RE blocker. Offline comparison established positive MTP
evidence in both SDKs: 6.45 uses a different setter that the first scan missed,
alongside E4 push distribution (corrected 2026-10-02).
Next: trace the push trigger or establish exact-device positive MTP provenance,
not repeat identical LAN probes. See [SDK evidence](docs/internal/yoosee-platform-sdk-versions.md).
Follow-up: SDK 6.45 advertises push relay on its broker A4; our default broker A4
does not, while explicit-metadata requests do. Copy/send chain and socket-free
option regression tests are documented. Do not globally enable an unimplemented
relay transport merely to solicit platform metadata.
Push teardown follow-up: SDK 6.45 reason-zero `03 0B` hangup layout/checksum is
implemented as a socket-free driver codec with synthetic tests. It differs from
B9 route release. E4 field provenance, outer timer cleanup and ready predicates
are mapped; a socket-free context parser correlates device and MTP link. Remaining:
connected-score statistic sources/scheduling, reconnect callback ownership, pre-ready remote
lifetime and acknowledgement semantics before any relay-enabled diagnostic; see
[teardown evidence](docs/internal/yoosee-push-teardown.md).
The single-terminal certification encoder and receive-side recertification
transition are now mapped offline; 77 synthetic tests pass. No relay transport is
enabled. See [certification evidence](docs/internal/yoosee-push-certification.md).
Follow-up: TCP/UDP selection, receive dispatch, timer intervals, recertification
retry branches and peer-limit notifications are documented. The future transport
must validate frames more strictly than the inspected SDK receive branch; these
offline results do not homologate camera-3 HD or prove remote cleanup.
The connected predicate uses a 0–75 quality score (>5), not an elapsed-time counter;
its arithmetic is mapped, with zero-denominator behavior still requiring care.
Reception follow-up: bounded socket-free TCP framing now handles fragmented and
coalesced reads with fail-closed EOF/admission tests. Connect/close callback
transitions are mapped; the score helper's execution is unproven because actual
ready-path timer registrations differ. Remaining: socket cancellation ownership,
terminal-mode callback asymmetry and remote lifetime. See
[reception evidence](docs/internal/yoosee-push-reception.md).
Socket-lifetime follow-up: node teardown disables internal reconnect, closes the
socket and invokes its close callback before freeing local storage. Parent-session
ownership must survive this callback. Queued callback/thread semantics and remote
release remain unproven; no live relay integration was enabled.
Callback-isolation follow-up: socket-free reception now uses per-connection opaque
generations, discarding old reads/EOF and partial input on replacement/cancel.
Read/event dispatch return semantics are mapped; downstream queued work and real
socket cancellation still require integration. This does not validate relay replies
or enable certification success automatically.
Static callback-signature follow-up: the mode-2 branch registers a three-argument
receive handler where the connect dispatcher only supplies the socket argument.
Reachability and terminal-mode meaning remain unproven; do not copy/execute that
SDK branch blindly or attribute historical crashes to it without runtime evidence.
Capture checkpoint: bounded content-free triage of the existing 12,436-packet PCAP
found no protocol-3 headers at inspected datagram/segment boundaries; no TCP
reassembly/decryption was performed, so absence is not conclusive. GAT E3 is a
separate broker response, not relay certification or proven release. See
[capture evidence](docs/internal/yoosee-push-capture-evidence.md).
Follow-up: all 60 payload-bearing TCP directions reconstructed continuously within
offline limits, still with no known protocol-3 header at their captured prefixes.
Application encryption/later offsets remain outside this evidence; no capability
or certification-success inference was made.
Historical MTP checkpoint: the same capture has 669 parsed meters, none with
positive platform bit `0x20`. No platform-1 fallback or HD capability was inferred.
The offline diagnostic now counts this evidence without exposing device/session IDs.
Platform follow-up: an E4 with a clear promotion bit now retains unknown/previous
platform evidence instead of inventing platform 1 or downgrading platform 2.
The SDK quality getter reads player cache, not camera-applied resolution; it is
not an alternative provenance source. 49 focused offline tests passed. Native HD
remains gated; see the linked SDK evidence for exact addresses and limits.
Session-correlation follow-up: passive E4 evidence now requires the current MTP
link as well as device identity; responses for other links cannot change the
platform. 63 focused tests passed. This does not resolve remote relay lifetime
or provide a positive camera-3 platform observation.
2026-10-02: the passive MTP collector now recognizes positive older-SDK platform
evidence only after a fully correlated sent-meter roundtrip. The internal AV
diagnostic preserves this optional value after cleanup. No extra packets or
automatic HD selection; historical capture still has no positive observation.
2026-10-03: a server-selected platform-only diagnostic reuses the reserved route
owner but stops after correlated MTP metering and B9 cleanup, before AV INIT/START
or decoding. 68 focused tests passed. One deployed camera-3-only observation on
2026-10-03 returned HTTP 200 with correlated metering and B9 receipt, but platform
remained null. No AV stream/control was started. Diagnostic disabled/targets
cleared afterward; health 200 and all three recorder MP4s growing. HD remains
blocked; do not repeat this probe or infer a legacy platform from silence. Next:
offline protocol work and independently proven platform provenance.

Priority: **P0** critical · **P1** high · **P2** medium · **P3** opportunistic.

2026-10-03 offline SD follow-up: command 25's four-byte strategy body is confirmed
in SDK 6.45; its getter returns requested local state, not a camera query. Reply
success requires nonempty payload starting with 1 or 2, unlike the mapped video
quality callback. Follow-up resolved the virtual startup caller: `play` replays
only a nonzero cached strategy; the constructor initializes it to zero. Command
25 is therefore not mandatory on the inspected default playback path. Enum
semantics remain unknown; do not send a guessed strategy to unblock SD listing.
No live builder/control is exposed. See [strategy evidence](docs/internal/yoosee-playback-strategy.md).
Status: `todo` · `wip` · `done` · `blocked`.

2026-09-30 recording metadata checkpoint: concurrent successful same-file codec
probes are coalesced; at most two ffprobe inspections run per server process, with
fixed-size admission bookkeeping. This reduces redundant subprocesses before
encoder admission, not the cost of cold HEVC conversion. See
[cache/probe identity](docs/internal/recording-cache-identity.md).
Follow-up: codec admission now has a shared one-second wait budget and explicit
HTTP 429 on saturation, preserving original downloads without probing.
Playback retry follow-up: failed media/play attempts now detach their source and
clear readiness so one explicit selection starts a fresh attempt; autoplay denial
and deliberate pause retain normal resume behavior. See
[lifecycle evidence](docs/internal/recordings-playback-lifecycle.md).
2026-10-01: post-start buffering now has a 30-second no-progress deadline,
explicit same-row retry with saved position, and cancellation/late-timer tests.
No automatic conversion retry or live-camera change.

PTZ update (2026-09-14): **user physically validated all four directions and fluidity
on camera 3**. Dashboard API validation also passed all four; warm alternating
actions took 391–411 ms end-to-end. Update 2026-09-15: driver-owned model/profile
selection now replaces per-unit opt-in for P2P-enrolled cameras; see
`docs/internal/ptz-model-selection.md`. Camera-3-only scope was for physical tests,
not a permanent support restriction. Cold setup still
adds latency. See `docs/internal/native-transport-preference.md` for current scope;
earlier right-only milestones below describe the previous stage.

---

## Milestone M1 — Live video quality (Feature 1)  ⟶ CORE DONE

DHCP recovery (2026-09-14): automatic bounded MAC rediscovery and manual-scan media
resync implemented; see `docs/internal/dhcp-address-recovery.md`. Follow-ups: identity
audit of apparently healthy reassigned IPs; driver-specific discovery for cameras
without a known MAC; targeted source replacement without whole-media reload.

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
| P1 | Prefer homologated native driver transports per camera/feature, retaining RTSP/ONVIF fallback. Camera-3 native finite-step PTZ and click/drag are physically validated in all four directions. Native media still requires continuous reception, bounded local fan-out and measured latency/resource/recording comparison before replacing RTSP. No movement fallback after an ambiguous START. See `docs/internal/native-transport-preference.md` | wip |
| P0 | **HD transport hardening** — prefer WebRTC; bounded/recoverable MSE queue; no 0.1× live playback fallback | done |
| P1 | **Control polish** (feedback): quality dropdown, PTZ D-pad, borders on all buttons, taller bar | done |
| P1 | Grouped per-camera control panel: image, audio, security, storage and maintenance; disabled placeholders with honest capability explanations, driver-filtered options, focus/scroll lifecycle and filtered server-recordings navigation. Separate semantic modules; see `docs/internal/dashboard-camera-controls.md` | done |
| P2 | Control-panel desktop/narrow-viewport Chromium review passed with synthetic capabilities and zero requests, including nested focus, permission gating, session cleanup and toasts. Fixed long names wrapping the close button. Physical mobile review, richer backend disabled-state reasons and SD integration after driver homologation remain | wip |
| P1 | Control application feedback: localized prominent spinner for selectors/schedule/alarm sound, cleanup after errors, and sanitized floodlight phase logs. Retained logs show three 502 light failures; root cause remains unproven without the new phase diagnostics. No automatic command retry | done |
| P1 | Fix night-vision/settings selectors returning to the group label after writes: retain only backend-confirmed semantic values; unconfirmed/error outcomes remain unknown. DOM regression coverage includes real select value/index semantics. Physical mode verification remains separate from this UI fix | done |
| P1 | Require verified semantic responses in alarm-voice and weekly protection dialogs too; freeze submitted inputs, reject duplicate submits, keep failures visible and editable instead of closing on HTTP 200 alone | done |
| P1 | Fix floodlight request/receipt sequence reuse: reserve disjoint sequence ranges, retain bounded readback/no actuation replay. Camera-3 host and deployed HTTP OFF→ON→OFF now pass with independent reads; user physical confirmation of this correction remains pending. See `docs/internal/camera3-controls-audit-2026-09-13.md` | wip |
| P1 | Camera-3 live control audit: user confirmed orientation, push-to-talk, recorded voice, siren pulse and four-direction native PTZ. Night vision awaits nighttime validation. Relative volume, schedule enforcement and individual alarm sounds remain separate confirmation items; do not infer them from API success | wip |
| P1 | Center camera controls in a responsive popup with internal scrolling, fixed close/status areas and mobile-stacked touch controls; clarify siren selection/activation and protection schedule labels in English/Portuguese. DOM/static tests passed; mobile visual review pending | done |
| P1 | Diagnose intermittent white-light 502 failures: phase logs plus live comparison identified overlapping B9/BA sequences; corrected driver passed exact-unit host and HTTP roundtrips. Physical light confirmation remains tracked separately; no automatic actuation replay | done |
| P1 | Native finite-step PTZ on camera 3: all four directions, automatic release, per-camera ownership and no fallback after attempted START. User physically validated the result. No automatic rollout to other units | done |
| P1 | PTZ latency/input: stopped-session reuse across reviewed directions (8s idle/20s absolute, four idle sockets max), fresh request identities and click/drag pad without movement backlog. Camera-3 fluidity physically validated; warm alternating HTTP actions 391–411 ms | done |
| P2 | Evaluate continuous native hold and reduced cold-start PTZ latency separately; do not weaken finite gesture/STOP safety or infer support on other cameras | todo |
| P1 | Shared PTZ D-pad independent of native/ONVIF transport: finite click/drag gestures on all PTZ-capable cards, with DOM and ONVIF dispatch contracts passed. Garagem/Quintal access-only P2P enrollment recovered from authenticated account inventory plus documented exact device/MAC associations; no vendor rebind or fabricated subscription token. Driver covers their observed 40.1.22 variant; live read-only native preparation passed in 2120/2082 ms, without movement. Physical response/STOP on this firmware remains a field check. See `docs/internal/existing-camera-p2p-recovery.md` and `ptz-model-selection.md` | wip |
| P2 | PTZ credential/session longevity: preflight now uses the existing one-shot renewal helper only on explicit stale-access rejection (0x216B), rechecks camera identity/cancellation and rebuilds the cache key from current access/device credentials. START/STOP remain outside renewal. Focused tests passed; actual expiration remains to be homologated. Determine vendor `expireTime` semantics and test longer route reuse before changing the existing 8s idle / 20s absolute limits; no indefinite sockets or automatic movement retries. See `docs/internal/ptz-credential-renewal.md` | wip |
| P2 | Discreet dismissible notifications for camera-control outcomes: right-side responsive toasts, 5-second timeout, explicit localized close button, three-message cap and active-dialog scope implemented. Keep applying spinners and form-validation guidance inline. DOM contracts passed; app rebuilt/deployed 2026-09-15, health OK. Real browser/mobile visual review pending | wip |
| P1 | Native video receive path: bounded KCP/MTP/V1 replay and independent HEVC/AAC decoding validated offline. Configured-IDR recovery passed three 120-frame scenarios; ungated control failed (74/120). Baseline/delayed-ACCEPT replays preserved 151 video records / 155 audio frames. Fresh route/meter/AV CLOSE/B9 cleanup and gated diagnostic implemented. Structured camera-3 live sample after green CI returned 502 in 3.83s: direct A4 ACK=false, meter ACK=true, two peer datagrams; B9 release receipt=true. AV INIT was not invoked. Diagnostic disabled/target cleared after collection; normal RTSP retained. Next: investigate absent/ignored direct-A4 receipt against SDK/intercom and classify bootstrap traffic if needed; do not weaken validation or increase timeouts blindly. Native live video remains unhomologated. See `docs/internal/native-av-bootstrap-investigation.md`, `native-av-first-live-attempt.md`, `native-av-operator-trigger.md`, `native-av-meter-diagnostic.md`, `native-av-route.md`, `native-av-close.md`, `native-av-probe.md`, `native-av-reorder.md`, `native-av-handshake.md`, `native-av-control-send.md`, `native-av-paired.md` and `native-video-recovery.md` | wip |
| P1 | Native video bootstrap follow-up: SDK LAN-A4 callback is non-gating; experimental AV requires a sent-meter roundtrip. Camera-3 classified sample on 2026-09-21 after green CI `e09a9d3`: HTTP 502/4.85s, two peer datagrams, labels `reply/wrong_call/wrong_role`; no sequence/timestamp rejection, no AV INIT, B9 receipt=true. Diagnostic disarmed, normal RTSP retained. Next: verify meter-reply extension layout against SDK/PCAP before changing role/call validation. Existing intercom unchanged; native live video unhomologated. This supersedes the older A4 investigation next-step above. See `docs/internal/native-av-sdk-bootstrap.md` | wip |
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

Native-video follow-up (offline, 2026-09-21): SDK reply construction zeroes rather
than copies the request extension; historical PCAP has 199 zero-tail 72-byte
replies and 159 tagged-tail 68-byte replies. Experimental bootstrap now recognizes
these exact reply layouts without relaxing route/sequence/timestamp checks.
**Live checkpoint passed 2026-09-21:** one camera-3 diagnostic on `afa5f04`
returned HTTP 200 in 6.23s, negotiation ready, 29 video records / 45 audio frames,
AV CLOSE and B9 receipts. Diagnostic disarmed; RTSP retained. This supersedes the
bootstrap-failure next steps above, but is not decoded/dashboard playback or
LAN-only authentication. Next: bounded real-media decoding/keyframe/timestamp
validation, then generic native-media adapter/fallback design. See
`docs/internal/native-av-first-success.md` and `native-av-sdk-bootstrap.md`.

Native-video decoding foundation (2026-09-22): explicit video-only in-memory sample
hook, 2 MiB/120 frames, configured-IDR start and failure cleanup implemented without
enabling retention in the live endpoint. Historical-PCAP replay through the new
collector independently decoded 120/120 HEVC 640×360 frames. Next: separately gated
route-owner retention/cleanup and decoder isolation, then one camera-3 live decode.
See `docs/internal/native-av-video-sample.md`.

Native decode opt-in (2026-09-22): server-only, disabled-by-default decode mode is
wired through exclusive route ownership. All route cleanup completes before
sequential memory/time-limited decoders; samples clear on every failure/success.
New runtime decoder passed historical-PCAP 120-frame validation. Next: green CI,
capped build and one reviewed camera-3 live decode; no deployment/live decode yet.
See `docs/internal/native-av-decode-trigger.md`.

**Real camera-3 decode passed (2026-09-22, `bd52dfc`):** one HTTP 200/6.72s sample,
44/44 HEVC frames independently decoded at 640×360 after AV CLOSE/B9/socket cleanup.
Sample cleared, both opt-ins disabled, normal RTSP retained. Next: map/prove native
maximum-resolution selection (640×360 is not the desired default), then generic
single-source/fan-out + RTSP fallback and sustained/browser validation. No native
dashboard capability enabled. See `docs/internal/native-av-first-live-decode.md`.

Native quality mapping (2026-09-22): APK/SDK prove LD/SD/HD/AUTO values and distinct
legacy command 5 (one byte) versus platform-2 command 0x33 (five packed 3-bit slots).
Socket-free payload encoder and negative tests implemented; no live sender or HD
support claim. Next: INIT/default-quality and response correlation before one
camera-3 maximum-resolution validation. See `docs/internal/native-video-definition.md`.

Startup/reply follow-up (offline): SDK copies 32-byte player userdata; legacy
quality is byte 0 and packed quality bytes 23–24. Current INIT defaults encode SD,
not an explicit HD request. SDK callback accepts empty/non-0xff reply bodies and
caches the requested value, so callback success cannot homologate resolution.
Next: prove rendezvous/INIT propagation and message correlation before a bounded
HD trial; no live/default behavior changed. Evidence and exact addresses are in
`docs/internal/native-video-definition.md`.

Startup quality preparation: immutable, platform-specific userdata editing added
with preservation/negative tests (38 focused cases total). Native A4 and INIT wire
destinations mapped; channel source assignment chain remains pending. No live
override, production default change or HD capability enabled. Keep the existing
SD-playback-only custom-metadata guard until live propagation is verified.

Live propagation verified offline: SDK argument → channel → A4/INIT copy chain
closed. Codecs now accept explicit live metadata without the playback flag; old
default packets stay unchanged. 91 focused codec/media/playback regression tests
passed. Next: thread the same immutable metadata through the bounded diagnostic
route and reliable INIT, with authoritative platform and camera-3 gating before
one HD trial. No runtime override or new dashboard capability yet.

Bounded quality transport (2026-09-23): optional immutable live userdata now passes
through rendezvous, direct setup, receive coordinator and reliable INIT. Invalid
metadata fails before socket creation; START/CLOSE stay unchanged and INIT retries
are byte-identical. 139 focused tests passed. No operator/API wiring, camera test
or deployment yet. Next: authoritative-platform profile policy under the existing
reviewed-camera gate, then one bounded HD decode trial.

**HD live trial blocked — evidence review 2026-09-23:** camera 3 still lacks an
authoritative platform enum. Historical exact-unit rendezvous/MTP collection
returned no E4, and the reviewed native decode results do not establish that enum.
The inventory `new_platform` flag, firmware string and 640×360 output must not be
substituted for it. Startup plumbing is implemented, but a camera-3 HD attempt is
not currently ready. Unblocking RE: recover the SDK exchange that delivers E4 (or
prove an equivalent platform source), correlate it to the exact device and retain
its provenance before enabling operator profile selection. Do not repeat the
same media probe or select both platform fields speculatively. The 2026-10-01
SDK comparison above refines the E4-only assumption; recordings polish is deferred.

## Dashboard reliability, settings and access security — requested 2026-09-21

2026-09-30 playback cache: replaced path-only cache identity with a source-version
key and before/after encoding checks. Growing/replaced originals cannot reuse or
publish stale conversions. Old derived entries need on-demand preparation again;
no originals deleted and no bulk work enabled. See
`docs/internal/recording-cache-identity.md` for scope and regression evidence.

2026-09-30: Settings now uses an extensible side-menu/tab shell instead of stacking
preferences and access management. Mobile uses a horizontal tab bar. Preferences
have their own module and preserve drafts while switching sections; access cleanup
runs when leaving its tab. Keyboard navigation, exclusive visibility and draft
preservation have automated coverage. See `docs/public/settings.md`.
User validated the deployed sidebar/tab workflow on 2026-09-30 (build
`b-bf1d86005bdf`); this is no longer awaiting local UI confirmation.

2026-09-30 recordings-view checkpoint: production list/player/overlay modules
passed first-click playback, seek/reselection without reload, rapid switching with
a late preparation response, download isolation and teardown in real Chromium at
desktop/narrow widths. Added a 15-second abortable list deadline and localized
retry guidance; no automatic retries. The fixture uses a local five-second H.264
file and synthetic APIs, not cold-HEVC conversion or full authenticated dashboard
homologation. See `docs/internal/recordings-browser-acceptance.md`.

These are acceptance/rollout criteria, not blanket security guarantees. Source
implementation has progressed beyond the original inventory: settings are available,
and delegated login/per-feature grants are now implemented and CI-tested. See the
dated checkpoints below for exact deployment/validation boundaries. Keep core
settings/authentication brand-agnostic; capabilities remain owned by drivers.

| Priority | Item / acceptance criteria | Status |
| --- | --- | --- |
| P1 | **Recordings playback**: lifecycle fixes, bounded startup, native fallback and Range delivery implemented. Chromium passed autoplay recovery, seeking to minute four with nine seconds buffered, and integrated Recordings-view selection/seek/cancellation/list-timeout at desktop and narrow widths. Source-identity cache and two-probe admission budget are deployed with green CI. Remaining: authenticated full-dashboard/proxy, physical mobile and representative end-to-end cold-HEVC measurements. Preserve downloads and bounded encoder/buffering budgets. See `docs/internal/recordings-browser-acceptance.md` | wip |
| P1 | **Settings screen**: environment inventory and revisioned two-field allowlist implemented/deployed, with localization and primary-only authorization. Desktop/narrow layout passed. Additional settings need separate service-owned application/impact review; main key, secrets and raw environment remain server-only. See `docs/internal/settings-dashboard-plan.md` | wip |
| P1 | **Multiple delegated keys**: owner-only modal, per-feature grants, optional expiry, one-time secret, hashed verifiers and revocation implemented/deployed. HTTP lifecycle and isolated Chromium passed. Remaining: full-dashboard physical mobile and proxy acceptance. Grants cover all cameras, never unsupported driver features | wip |
| P1 | **Expiry/revocation**: fresh DB-backed HTTP checks, guarded MSE/intercom/files and session watcher implemented. Concurrent MSE tests close two affected sessions while another key continues; nested dialogs also clean up. Remaining: browser multi-tab/proxy and deployment restart acceptance. Channel checks are bounded periodic enforcement, not zero-latency recall of buffered data | wip |
| P1 | **Authentication abuse resistance**: bounded login pacing/requests, cryptographic keys, cookie and origin policies implemented and regression-tested. Remaining: validate the actual HTTPS proxy/trusted-forwarding deployment; no production brute-force tests or blanket internet-safety claim. See `docs/internal/login-abuse-protection.md` | wip |
| P1 | **No bypass / secret exposure**: default-deny route tests, static allowlist, sensitive-error projection and Docker-context audit implemented; local public-path checks passed. Continue auditing new routes, proxy behavior and diagnostics. Secrets, DB/backups, RE/temp and VCS must never become static assets; frontend hiding is never authorization. See `docs/internal/public-file-audit.md` | wip |
| P1 | **Security suite / deployment guidance**: CI covers both key types, grants, expiry/revocation, channels, origin and abuse boundaries. Guides document limits. Remaining: real mobile/proxy/channel acceptance, separate from automated evidence; login alone does not certify internet exposure | wip |

Recordings follow-up (2026-09-23): frontend selection/player lifecycle repaired in
an isolated module: cancel stale requests/polling on navigation/logout, preserve
seek on repeated selected-row clicks, request play without a loadedmetadata-only
trigger, and show a discreet localized status after autoplay rejection while
retaining native video controls (the extra button was removed after a CSS/hidden
regression reported by the user). Fake-media
Node regressions added to CI; 42 focused Python contracts passed. The reported
browser symptom is not yet physically homologated, and uncached full-file HEVC
conversion still causes startup delay. Next: bounded conversion/timing audit and
real-browser validation; see `docs/internal/recordings-playback-lifecycle.md`.

Recordings resource follow-up: shared process-local conversion budget now covers
foreground and warmer: one encoder, four admitted jobs maximum, bounded queue wait,
single-thread decoder/encoder/filter settings and explicit HTTP 429/UI busy handling.
Content-free queue/encode timings added; cache and full seekable MP4 retained.
54 focused Python tests and Node contracts passed; bounded synthetic conversion
passed. Actual-camera first-open latency/browser validation and deployment remain
pending; do not equate resource hardening with a measured startup-speed fix.

Recordings measured follow-up: one closed camera-3 1080p HEVC archive (300s) took
22.1s to convert completely under 512 MiB/one CPU; this directly contributes to
first-open delay. Ten ASGI Range/auth/If-Range tests validate seekable delivery.
Added a bounded stat-identity codec cache to avoid repeated ffprobe (one measured
lookup: 264ms cold vs 0.058ms cached). Original media untouched. Deployment and real
browser timing/first-click validation remain pending; see
`docs/internal/recordings-playback-measurements.md`.

Recordings deployed checkpoint: runtime `9a092c5`, build `b-c424c1dc6a43`, app-only
recreation with go2rtc unchanged. Actual authenticated HTTP check: cold archive
ready in 18.1s, cached prepare 3.54ms, three valid 4KiB ranges in 3–6ms, unauthenticated
access 401. Three base streams retained one producer each and advancing counters.
Browser first-click/seek validation is still pending. INFO preparation metrics were
not visible in default container logging; scoped logging remains a follow-up.

Scoped preparation logging is now implemented and tested under default Uvicorn
configuration: one stderr event/job, fixed failure reasons, no paths/credentials,
no global verbosity increase. 35 focused tests passed; deployed 2026-09-24 as
build `b-37d304904a08`, with bounded build/isolated synthetic image logging check,
health 200 and go2rtc unchanged. Next:
browser first-click/seek validation and client/server timing correlation; the
18–22s uncached full-conversion wait is still an open performance tradeoff.

Native recordings follow-up (2026-09-24): codec-specific browser hints now opt into
authenticated original HEVC/MP4 delivery without encoding; unsupported/failed
native playback falls back once to the shared H.264 cache. Range/auth/path and
fake-media lifecycle regressions cover the new path. No universal HEVC support
or real-browser success is assumed. See `docs/internal/recordings-native-playback.md`;
desktop/mobile native playback and fallback homologation remain pending.

Isolated Chromium validation passed compatible playback and native-error fallback,
seek to 3s, same-row resume without reload, and disposal, using a five-second local
derived fixture and the real controller. Browser process tree capped at 512 MiB,
no swap/75% CPU/65s; peak 465.1 MiB, exited after 5.45s. No production dashboard or
camera connection. Native HEVC success, default autoplay policy, mobile and full
dashboard UX remain unverified. Intentional-pause `AbortError` now clears status
instead of reporting a false failure; covered by a separate fake-media regression.

Settings inventory checkpoint (2026-09-24): all 42 declared fields classified in
`docs/internal/settings-inventory.json`, with CI coverage for completeness and
server-only credential/operator boundaries. Application lifetimes, inert S3/timeout
fields, destructive retention risks and desired/effective-state design documented
in `docs/internal/settings-dashboard-plan.md`. This is not an editable settings
screen or a hot-reload implementation. Next: trusted session principal and
primary-key-only management authorization, before any settings/key write endpoint.

Documentation synchronization (2026-09-24): root/docs/internal READMEs, contributor
instructions, recording API reference and ADR 0013/0021 amendments now link the
native-playback, bounded-conversion, browser-validation and settings-inventory
evidence. Added `docs/public/recordings.md`. Removed current-feature claims for
unimplemented S3, clarified seven-day default retention, and distinguished planned
settings/temporary keys from deployed behavior. No runtime configuration changed.

Session-principal checkpoint (2026-09-24): versioned primary-key sessions now carry
random identities, strict signed-payload validation and a primary-only management
dependency. Exact legacy cookies retain existing access without management elevation
or lifetime extension. `/me` adds safe origin/permission hints; no key/session ID is
returned. 43 focused tests passed, including negative management cases and legacy
expiry. Backend deployment remains pending; no settings/key-management endpoint,
temporary key, revocation store or active-channel invalidation was added. See
`docs/internal/session-principal.md`. Next: allowlisted settings persistence/application
behind this gate, with remaining security prerequisites before temporary-key activation.

Runtime-settings checkpoint (2026-09-24): two-field SQLite overrides and GET/PATCH
`/api/settings` implemented behind the primary-session gate. Strict schemas,
revision conflicts, JSON/same-origin write checks and baseline restoration tested;
media metadata/cache policy consume overrides on their next read. 86 focused
regressions passed. No production override/restart, no secret/retention controls,
no settings UI yet; deployment pending. See `docs/internal/runtime-settings.md`.
Next: localized settings view with conflict handling and application-scope notices.

Settings UI checkpoint (2026-09-24): dedicated en/pt-BR settings module with two
bounded inputs, per-field environment-default reset, primary-only editing, explicit
save, conflict/uncertain-save reload and navigation/logout cleanup. No credentials
or retention controls. Node contracts added as a sixth CI gate; 45 focused Python
tests passed under a whole-process 512 MiB/no-swap cgroup (75.8 MiB peak). An earlier
RLIMIT_AS attempt prevented Node's virtual reservation; standalone Node contracts
and the corrected cgroup run passed. No WSL OOM observed. Deployment/mobile visual
validation pending; see `docs/public/settings.md` and `docs/internal/runtime-settings.md`.

Settings deployment checkpoint (2026-09-24): `f51b21c`, image `b51391ac1d18`, build
`b-26db71224da6`; all six CI gates passed. Bounded build and app-only recreation,
go2rtc unchanged. Actual settings GET: anonymous 401, legacy 403, primary 200;
revision 0/no overrides, no production PATCH. Health 200 and three consumed streams
with one producer each/advancing bytes. Sign out/in with the primary key to edit.
Public guide/readmes updated; mobile visual validation and temporary-key security
remain pending. Details: `docs/internal/runtime-settings.md`.

Settings layout checkpoint (2026-09-24): compact description/value rows, 112 px
desktop / 88 px mobile inputs, visible disabled defaults and separate actions.
Real Chromium isolated-component checks and screenshot inspection passed at
1280×900 and 390×900; bounded browser runs peaked at 399 MiB with zero swap.
Full-dashboard/physical-mobile validation remains distinct. Temporary-key issuance,
expiry and revocation remain pending. Details: `docs/internal/runtime-settings.md`.

Temporary-key foundation checkpoint (2026-09-24): small platform service/repository
modules implement random credentials, verifier-only SQLite records, UTC expiry,
bounded metadata listing and idempotent persistent revocation. 58 focused auth/key
tests passed, including concurrent revocation and storage failure. No temporary
login/API/UI enabled, production key created or camera contacted. Next: primary-only
management boundary, temporary permission policy, session linkage and live-channel
invalidation before activating login; see `docs/internal/temporary-access-keys.md`.

Temporary-key management API checkpoint (2026-09-24): primary-only creation,
paginated metadata listing and idempotent revoke routes registered; strict input,
no-store including handled errors, same-origin/JSON writes and sanitized failures.
94 focused API/lifecycle/settings tests passed. API not yet deployed; no production
keys created and temporary login/UI remain disabled (`login_enabled: false`).
Next: permission policy and session/channel invalidation before activation.
Details and uncertain-create retry guidance: `docs/internal/temporary-access-keys.md`.

Recording loading-feedback checkpoint (2026-09-24): localized spinner overlay in
`rec-main`, nonblocking and scoped outside the recording list. Preparation/startup/
buffering state clears on playback, pause, error/autoplay denial and disposal.
Node lifecycle regressions and capped Chromium desktop/mobile component checks
passed; actual short H.264 playback/seek and native-error fallback clear loading.
No extra encoder/request/timer or container restart. Long-recording/native-HEVC/
physical-mobile validation remains separate; see playback lifecycle notes.

Open-channel checkpoint (2026-09-24): shared one-second session revalidation for
media/intercom sockets, five-second verification timeout, fail-closed 1008 and
owned-task cleanup. 41 focused fake-channel/intercom/principal tests passed; no
camera commands or container restart. Backend deployment and temporary login remain
pending. Dashboard transport confirmed MSE (obsolete WebRTC comment corrected);
independent WebRTC peers/in-flight HTTP delivery and explicit temporary permissions
still need enforcement. See `docs/internal/session-channels.md` for scope/latency.

Temporary-session linkage checkpoint (2026-09-24): internal credential-verified
issuer and signed key-ID binding, fresh key checks per verification, all derived
cookies invalidated by key expiry/revoke. Primary checks remain independent of DB.
Transitional exact-route/method allowlist denies uncovered operations; channel gates
reject temporary sockets before work. 141 focused tests passed, including actual app
HTTP/WS denial. Not deployed; public login/UI disabled. Next: final permissions,
in-flight delivery, dashboard invalidation and abuse protection before activation.
Details: `docs/internal/temporary-sessions.md`.

Dashboard invalidation checkpoint (2026-09-25): dedicated non-overlapping `/me`
watcher, one-second post-check interval, focus/pageshow checks and five-second abort
deadline; invalid sessions return to login. Stale response/boot guards prevent player
reactivation, and controls/voice/PTT cleanup includes late microphone grants. Four
Node suites and 74 focused Python tests passed; new lifecycle suite is CI gate seven.
No camera/container operations. Full browser/multi-device validation and server-side
in-flight delivery remain pending; temporary login disabled. See session-watch notes.

Recording authority checkpoint (2026-09-25): temporary file/download delivery now
owns a periodic validity watcher and cancels on invalidity even under ASGI send
backpressure. Starlette Range/206/multipart/If-Range semantics retained; pathsend
disabled only for temporary transfers. Temporary prepare/file/download routes now
allowed internally; login and WebSockets still disabled. 84 focused tests passed;
no production/camera/container operations. Proxy/browser interruption validation and
remaining activation gates pending. See `docs/internal/recording-session-delivery.md`.

Temporary live-media checkpoint (2026-09-25): dedicated registered-source MSE bridge
with bounded handshake, no arbitrary upstream commands/WebRTC, fresh key/registry
checks and relay cancellation. 71 focused tests passed without camera/container
operations. Public temporary login remains disabled; backend deployment, real
browser/proxy validation and other activation gates remain pending. Details:
`docs/internal/temporary-live-media.md`.

Login-abuse checkpoint (2026-09-25): fixed-memory origin pacing before login body
parsing, atomic burst/refill, no quota reset on success, generic 429/Retry-After and
localized feedback. Bundled launcher disables forwarded-header identity rewriting;
proxy clients currently share quotas. No cameras/production login/container changes.
Body/resource limits, explicit proxy/cookie/CSRF policy and final temporary permissions
remain open; temporary login is still disabled and backend deployment pending.
See `docs/internal/login-abuse-protection.md` for collision/multi-worker trade-offs.

Login-request checkpoint (2026-09-25): bounded 16 KiB body collection with a total
5-second deadline, 8 in-flight handlers, generic credential-safe validation errors,
localized busy/timeout feedback and HTTPS-transport Secure cookies. 202 focused
tests passed, no production/camera/container operations. Proxy/public-origin policy,
consistent CSRF protection and final temporary permissions remain pending; login
activation/deployment still deferred. See `docs/internal/login-request-boundaries.md`.

Browser-origin checkpoint (2026-09-25): shared source/target validation for login,
logout, authenticated writes and media/intercom sockets; optional server-only
`DASHBOARD_PUBLIC_ORIGIN` pins proxy Host/origin and HTTPS cookie issuance without
trusting forwarded client identity. Local-only controls remain restricted. 284 focused
tests passed without physical camera/container operations. Real proxy/browser rollout,
exceptional-route/GET-mutation and static-secret audit, final temporary permissions
and login/UI activation remain pending. See `docs/internal/browser-origin-policy.md`.

Public-file/route checkpoint (2026-09-26): static public asset allowlist, asset symlink
denial, hidden/non-MP4 archive rejection and expanded Docker context secret/backup
exclusions. Explicit public/custom-auth route inventory added. 119 focused tests
passed without production file/camera/container operations. Outstanding finding:
legacy GET `/recordings/file` can still initiate compatibility conversion; move that
initiation to POST `/recordings/prepare` with compatibility coverage. Deeper response
redaction, browser/proxy validation and temporary-login rollout remain pending.
Details: `docs/internal/public-file-audit.md`.

Recording GET checkpoint (2026-09-26): media GET no longer starts/restarts conversion;
missing compatible output returns 409/no-store and requires explicit POST preparation.
Dashboard ordering already conforms; legacy API migration documented. Range, original
delivery, downloads and shared POST admission preserved. 95 focused tests and Node
playback contracts passed without production camera/file/encoder/container operations.
Backend deployment and broader response-redaction/temporary-access gates remain open.
See `docs/internal/recording-get-boundary.md`.

Public-error checkpoint (2026-09-26): shared fixed-message control/HTTP/audio error
projection, safe vendor-account login/refresh failures and selected exception-type-only
logs. 129 focused tests passed with synthetic secret-bearing exceptions and fake
hardware dispatch. Backend not deployed. Next priority: remove discovery credentials
from query URLs; then remaining provisioning errors, sensitive validation responses,
successful driver payloads and SDK logs. See `docs/internal/public-control-errors.md`.

Discovery-credential checkpoint (2026-09-26): scan credentials moved to optional
bounded JSON; query parameters rejected without scan, bodyless dashboard flow retained,
generic validation errors and SecretStr password handling added. Bundled Uvicorn HTTP
access logs omit queries, including rejected legacy URLs. 93 focused tests passed with
fake scanners; no LAN/camera/container operations. External proxy/custom-launcher logs,
sensitive validation in other routes, provisioning errors and driver payload/log audit
remain pending. See `docs/internal/discovery-credential-body.md`; backend not deployed.

Sensitive-validation checkpoint (2026-09-26): main-app HTTP request schema errors now
return fixed 422/no-store responses without rejected values or validator context.
Synthetic body/JSON/path/query coverage added; authentication failures preserved.
No camera/container operations or backend deployment. Provisioning business errors,
successful payloads, SDK logs and browser/proxy rollout remain pending. See
`docs/internal/sensitive-validation.md`.

Provisioning-error checkpoint (2026-09-26): Wi-Fi domain now supplies typed failure
reasons; manual/QR/BLE network-selection errors use fixed recovery messages. QR
provider errors no longer echo exception text. 84 focused tests passed (87.4 MiB
peak, no swap), with mocked hardware/scans. Source only. Label resolution, BLE
material/session, privileged/P2P completion errors and successful payload/log review
remain pending. Details: `docs/internal/provisioning-public-errors.md`.

BLE-error checkpoint (2026-09-26): driver-neutral typed input reasons now preserve
expiry/renewal/wrong-camera/file-permission recovery guidance without exposing raw
codec messages. Handled prepare/decode failures use fixed 422/502/503 no-store
responses. 110 focused tests passed (82.2 MiB peak, zero swap), no hardware/container
operations. Label resolution and privileged/P2P errors, successful payloads and logs
remain pending. Source-only; see `docs/internal/provisioning-public-errors.md`.

Identification/enrollment-error checkpoint (2026-09-26): fixed no-store projections
replace raw label/driver-resolution, handled privileged state/transport and completion
errors. Untrusted completion stage strings are no longer returned. HTTP 422/409/502
semantics preserved. 113 focused tests passed, one non-applicable case skipped;
100 MiB peak, no swap. No hardware/container operations. Successful payloads,
unexpected exceptions, lower-level logs and backend/browser rollout remain pending.
See `docs/internal/provisioning-public-errors.md`.

BLE-payload checkpoint (2026-09-26): Wi-Fi connection reply `0x85` now allowlists a
strict signed integer status rather than forwarding arbitrary fields minus confirmKey.
Malformed statuses cannot mark a connection successful; raw field names/statuses
removed from decoder logs. 91 focused tests passed (86 MiB peak, zero swap), synthetic
inputs only. Wi-Fi-list/link-type payload contracts, other successful driver results,
SDK logs and deployment remain pending. See `docs/internal/provisioning-public-errors.md`.

BLE-network-metadata checkpoint (2026-09-26): explicit Yoosee `0x81` Wi-Fi list and
`0x73` link-type projections, bounded entries/fields, no raw text/hex fallback.
105 focused tests passed; 78.1 MiB peak, no swap, no hardware/container operations.
Alternate firmware schemas need explicit mapping/acceptance before deploying; broader
driver/privileged successful payloads and SDK logs remain pending. Details in
`docs/internal/provisioning-public-errors.md`.

Enrollment-status checkpoint (2026-09-26): generic API now projects explicit status
fields with strict booleans/expiry and camera-identity matching; extra driver fields
are discarded and malformed results fail with fixed 502. Status domain errors use
safe 409/502 messages. 104 tests passed, one existing non-applicable case skipped;
88.1 MiB peak, zero swap. No hardware/deployment. Inventory/route/property/completion
success projections and full SDK log audit remain pending. Contract documented in
`docs/internal/provisioning-public-errors.md`.

Driver-response/log checkpoint (2026-09-26): online/inventory/route scalar validation,
reviewed completion metadata (raw stream path removed), safe media proxy failure
logs and fixed AAC encoder errors. 148 focused tests passed (120.1 MiB peak, no swap),
Ruff/mypy passed. No hardware/deployment. Privileged raw-property diagnostics remain
operator-only, not a public privacy contract. External process logs and catalogue
privacy remain broader work. See `docs/internal/driver-response-log-audit.md`.

Camera-catalogue checkpoint (2026-09-26): public capability display projection and
raw stream-path omission; private driver evidence remains available to driver-owned
control/support decisions only. 87 focused tests passed (98.2 MiB, no swap). No
hardware/deployment. External-client migration required for removed internal fields;
see `docs/internal/driver-response-log-audit.md`.

Private-media-config checkpoint (2026-09-27): generated go2rtc credential config is
written 0600 (including unchanged-content permission repair), without replacing its
Docker-mounted inode; final symlinks/non-regular targets rejected. 49 focused tests
passed (83.4 MiB peak, zero swap), Ruff/mypy passed. No live files/processes touched.
Custom UID ownership, symlink migration and controlled backend rollout need review;
see `docs/internal/private-media-config.md`.

Safe-diagnostics checkpoint (2026-09-27): versioned safe one-shot watcher wrapper
added, without camera ffprobe/config/API/log dumps (ignored local helper also updated). Watcher omits
argv and raw errors, bounds stream API input and re-filters event history. Backend
client telemetry now has an explicit field/stream contract. 27 focused tests passed
(67.1 MiB peak, zero swap); no live collection/hardware/deployment. Historical support
bundles remain untouched. See `docs/internal/safe-stream-diagnostics.md`.

## Milestone M2 — Architecture & code quality (Feature 2)

| Priority | Item | Status |
|---|---|---|
| P1 | Tooling: `ruff` (focused ruleset) + `mypy` (clean, 31 files) + CI running lint+types+tests | done |
| P1 | `black` — config kept in pyproject, but **deliberately not applied**: a repo-wide format is ~887 lines of pure churn that undoes the author's intentional compact style (which the `ruff` ruleset is configured to allow). Lint/format bar is met by ruff+mypy. Available for anyone who wants it. | done |
| P1 | Test coverage ≥ 90% — **REACHED: 65% → 91%** (302 tests). Every module ≥ 89% (drivers/device/media 100%, storage 97%, recorder 93%, main/rtsp 92%, playback 91%, ws_discovery/routes 88–89%). Only scattered error-branch lines remain uncovered | done |
| P1 | Formalise the driver layer (Strategy + Factory) — **already done**: `CameraDriver` + ordered registry + `detect`/`for_camera`/`get` + generic fallback | done |
| P2 | Audit and organize partial vendor drivers: Hikvision, Dahua and XiongMai now own packages with `driver.py` and compatible public exports. Existing vendor detection/RTSP candidates retained; controls, two-way audio, SD and onboarding remain explicitly unimplemented. Registry/multi-brand/fallback and false-capability regression tests added; contributor guidance updated. No hardware feature grants. See `docs/internal/driver-package-audit.md` | done |
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
Dynamic resource proof storage is now implemented separately from static profiles: mandatory
expiry, exact identity/revision, per-selection provenance, bounded/revalidated JSON and atomic
newer-review replacement. Revocation survives expiry and cannot be undone by older reviews.
The reviewed historical summaries prove reversible selection but omit native resource IDs;
no production resource grants were imported from names/logical keys. Remaining: bind original
native identities or record a fresh exact-resource proof, then integrate validated observations
and enforce the same selection intersection in enumeration and pre-write resolution.
Fresh camera-3 silent validation now binds the missing native identities: Zumbido 1→Zumbido 2→
Zumbido 1, ACK/error zero and independent correlated B7 exact-ID/full-state restoration. Two
resource proofs were registered locally with 30-day expiry and verified against a fresh catalogue;
no playback action or other camera was involved. Strict C0/C1 mode now correlates inner encrypted
session/request after bounded fragment reassembly and rejects partial/misclassified catalogues.
Runtime dynamic enforcement is now deployed and explicitly enabled only on camera 3: listing and
pre-write resolution share strict fresh catalogue/proof intersection, rechecking revocation and
expiry after I/O. Correlated preflight/readback compares full native resource identity, not only
the logical slot. The production HTTP list returned only the two proven effects, without playback.
299 focused tests passed; app-only build `b-14c9a2bbd7e4` left the media service running.
The redundant startup refresh is fixed: valid exact-identity snapshots retain their existing TTL
instead of being invalidated on dashboard requests. Truly missing/expired evidence stays blocked;
a generic driver explanation hook maps temporary Yoosee evidence absence to HTTP 409 rather than
claiming unsupported hardware (501). No stale grants or automatic write retries. 341 focused tests
cover scheduling, invalidation and read/write/options dispatch. Next: migrate remaining
siren/intercom gates; physically validate only camera 3 when needed.
Siren migration now has an offline strict boundary: exact timestamped `Action.expelCtrl.stVal`,
correlated B7 preflight/readback, encrypted session/message-correlated AC/AD and sequence-matched
ACKs. Single ON and unconditional OFF cleanup remain covered. It is opt-in and not yet enabled
in runtime rollout; no new sound or camera test was performed. Next is a harmless camera-3 state
read with exact identity, then a bounded strict pulse before registering operation proofs.
Audio needs separate route-specific proofs; RTSP coordinates/P2P enrollment are not capability
evidence. See `docs/internal/yoosee-siren-capability-migration.md`.

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
| P1 | Camera-card recordings — generic opaque-ID/UTC/bounded-page contracts and fail-closed Yoosee `tfInfo` parsing are implemented. Camera 3's real card repeatedly returns APK-defined normal status `1`, a stable card ID and coherent capacity/free values; falling free space proves active onboard recording. The feature stays hidden until a harmless listing probe proves this exact camera/profile. Bounded codecs cover modern V1 command `0`, V2-V4 file command `16`, V2-V4 date command `18`, and internal V3/V4 recording-type command `15`, including V4 fragment assembly. A targeted decompile of Google Play 6.45's real `VSdcardPlaybackVM` proves its gate is only online/AP, non-battery-drain and `tfInfo.stat == 1`; it does not gate by `devFuncCfg`, model or firmware and constructs a V2 daily list with `cameraId=-1`, 500 items and the native ten-second window. The vendor parser now safely accepts that exact 500-item ceiling without expanding the generic 200-item API contract. Native RE pins the exact B9 carrier and both SDK routes: authenticated broker mode 2 plus direct known-LAN mode 1/bit 25. BA is a GAT receipt, not proof of application execution. Camera 3 acknowledged the exact UI-shaped V2 request on both routes but returned no application page even after the prior local five-second clamp was corrected to ten seconds. A camera-3-only, metadata-sanitized Frida hook is ready to capture the official SDK callback/error from one SD-screen visit; until that evidence, the runtime gate and dashboard capability remain closed. Positive, correlated MTP replies and decrypted GAT type `E4` are mapped platform sources in both inspected SDKs; passive collection preserves unknown values and still needs camera-3 evidence. Card-download commands 19–23 and thumbnail commands 26/27 have socket-free, correlated, bounded codecs without live entrypoints or whole-file buffering. Playback lifecycle has strict stream-begin/EOF decoders, pause/resume/seek builders and platform-specific 1×/2×/4×/8× speed codecs. Native `SDPlaybackPlayer` connection type 2, exact 32-byte A4/AV INIT userdata and `PushStreamDistribute` option `0x4000` are encoded without changing live-view golden frames. Command-25 strategy remains unmapped. The legacy `3→4` parser remains gated by its signed-32-bit-ID manager. Delete (`28/29`) and format remain disabled | wip |
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

2026-09-27: delegated access now has optional expiration and explicit per-resource
grants, with a primary-only responsive creation dialog and metadata/revocation UI.
Server checks grants independently of the dashboard. Follow-up completed delegated
login, permission-aware navigation and guarded intercom/MSE. Implementation is
source-tested and deployed locally as `b-a84862f46bfb`; real HTTP lifecycle checks
passed, all three cameras resumed recording, and browser/proxy/modal-layout acceptance remain.
See [activation/rollout](docs/internal/delegated-access-activation.md).

2026-09-27: prepared-recording playback now terminates an unresolved browser startup
after 30 seconds with an explicit retry, without automatic conversion retries.
Node lifecycle and 41 focused Python tests passed; browser acceptance remains pending.
See [playback lifecycle](docs/internal/recordings-playback-lifecycle.md).

2026-09-28: delegated-access modal passed isolated real Chromium at 320×568,
375×667 and 1280×900 after fixing oversized checkboxes. Creation/grants/secret
cleanup/navigation and sticky close were exercised with synthetic API data. The
settings fixture imports are now regression-tested; browser runs require cgroup
caps and direct ELF launch. Physical mobile/proxy checks remain. See
[browser checkpoint](docs/internal/delegated-access-browser-check.md).

2026-09-28 recording acceptance: real Chromium passed autoplay denial/trusted-gesture
recovery without extra preparation, plus seeking to 240 seconds in a 300-second
H.264 fixture when only nine seconds were buffered (64 KiB/s fixture transport).
No camera traffic or recoding; peak browser memory 250 MiB, zero swap. Full-page,
physical mobile and real cold-HEVC conversion acceptance remain; see
[browser evidence](docs/internal/recordings-browser-acceptance.md).

2026-09-28 fresh-process access checkpoint: four isolated interpreter launches
preserved grants/no-expiry and enforced revocation/exact expiry from SQLite while
keeping primary access independent. No service restart or production credentials.
Actual deployment restart/browser reconnect acceptance remains. See
[activation evidence](docs/internal/delegated-access-activation.md).

2026-09-28 control-dialog lifecycle: siren-selection and protection-schedule overlays
now close on session end and suppress late completion/error feedback after close.
Detached submit handlers cannot replay writes. Eight Node races plus a 375×667
Chromium synthetic-read test passed; no real camera action. See
[control panel evidence](docs/internal/dashboard-camera-controls.md).

2026-09-28 delegated-access polish: secret visibility timeout now explicitly differs
from revocation, without duplicate copy instructions. The operator diagnostic flags
an unknown creation outcome after a lost response and never retries creation or
revokes by non-unique label. Node UI and isolated ASGI regression coverage added;
see [activation/operations](docs/internal/delegated-access-activation.md).

_Convention: when an item is done, mark it `done` and move the technical detail/rationale into an ADR
under `docs/internal/`._
