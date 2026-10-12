# Internal documentation and architecture decisions

The *why* behind non-obvious design choices — one decision per file, in **Context / Decision /
Consequences** form. Reference them from code as `docs/internal/NNNN-...md`.

These are being distilled from the historical [`../DECISIONS.md`](../DECISIONS.md) log (a single
numbered journal). The ADRs below are authoritative for the decisions they cover; `DECISIONS.md`
remains the fuller narrative record and the source for decisions not yet migrated.

## Index

Native relay RE (experimental, not production capabilities): [RTC boundaries and
decryption](yoosee-push-rtc.md), [fragment ownership and assembly limits](yoosee-push-fragments.md).
See also [pinned SDK codec enum translation](yoosee-rtc-codecs.md).
Camera-3 live milestone: [native AV over TCP relay](native-av-tcp-relay.md)
completed negotiation and decoded HEVC at 640×360, then **1920×1080** with the
SDK-mapped HD startup profile and CLOSE transport receipt. Sensor-maximum
verification, sustained reception and production integration remain
pending; this is not LAN-only operation.
The [continuity/cleanup checkpoint](native-tcp-cleanup-checkpoint.md) records a
three-second HD follow-up, offline buffering regressions and unresolved broker release.
It also records the SDK-backed B9 delivery-kind correction and encrypted receipt
tests; a subsequent live check still did not confirm broker release.
Later investigation identified a shared-account isolation gap: standalone probes
do not share server locks. The internal diagnostic now reserves both camera and
broker; further TCP cleanup tests must continue using this in-server reservation.
The finite paired-socket receiver, rendezvous/socket owner and guarded internal
trigger are integrated. One isolated live check decoded 55 HD frames and confirmed
both CLOSE and B9 receipts; production streaming is unchanged.
The [duration budget review](native-tcp-duration-budgets.md) lists the layered
limits that must be reconciled before sustained reception; none were relaxed.
Follow-up: the reviewed extended profile passed one ten-second camera-3 run
(160 video records, largest arrival gap 312 ms, both cleanup receipts). See that
document for profile-specific bounds, restored state and production ownership limits.
The [ownership migration plan](native-stream-ownership-plan.md) maps the current
PTZ cache and certification paths before introducing a long-lived native source.
Its first code unit, [broker receipt correlation](broker-transaction-correlation.md),
is bounded/offline-tested and not yet used by production controls.
The [finite read dispatcher](broker-read-dispatch.md) now composes fragmented input,
transport receipts and application waiters without creating sockets or workers.
The [exclusive read socket adapter](broker-read-socket.md) adds bounded I/O and
guaranteed physical socket cleanup; production entry points remain unchanged.
The [same-process read pilot](broker-read-diagnostic.md) composes reviewed enrollment,
authentication and both reservations; its single-use, primary-session/loopback
trigger stays disabled by default and returns no raw identity values. One in-server
camera-3 invocation completed all three reads with positive transport receipts and
zero application errors. Restored disabled/empty; all three recordings grew afterward.
The [shared sequence namespace](broker-sequence-ownership.md) now prevents duplicate
allocation across future account consumers and rejects late replies after retirement.
This is offline-tested plumbing, not persistent heartbeat or PTZ integration.
Targeted [heartbeat SDK evidence](broker-heartbeat-sdk.md) identifies interval-field
bounds and 35/40/50-second timer selection, with default50. Response correlation
is now pinned to A1 field0x10 and separate ACK callbacks. A bounded single-pending
heartbeat waiter is implemented; the subsequent live check is described below.
The [finite account lifecycle](broker-account-lifecycle.md) now composes shared
reads/heartbeat, reserved maintenance capacity and bounded draining/retirement.
It has no socket or production wiring; the exclusive encrypted socket check below
provides the camera3 before/after-heartbeat validation.
That [socket check](broker-account-check.md) is now implemented and offline-tested,
with a disabled server-only opt-in. One camera-3 run passed all six identity reads
around a correlated heartbeat(interval50), with no recertification. Restored disabled;
production concurrency/native media integration remain pending.
The [bounded mailbox](broker-mailbox.md) adds offline-tested concurrent read
admission with exact wire binding and acknowledged cancellation. The
[exclusive finite worker](account-read-worker.md) is now implemented/tested offline,
including concurrent callers and two-second drain; it is not runtime-wired.

| ADR | Decision |
|---|---|
| [0001](0001-pluggable-camera-drivers.md) | Pluggable camera drivers (the core extensibility architecture) |
| [0002](0002-mac-keyed-camera-identity.md) | Superseded for public identity; authoritative MAC discovery retained |
| [0003](0003-layered-gentle-discovery.md) | Layered, gentle camera discovery |
| [0004](0004-crash-safe-fragmented-mp4-recording.md) | Crash-safe recording via fragmented MP4 |
| [0005](0005-live-view-transcode-and-codec-ceiling.md) | Live view: go2rtc, mandatory transcode, codec/CPU ceiling |
| [0006](0006-bounded-ffmpeg-oom-containment.md) | Bounded ffmpeg + cgroup limits to contain OOM |
| [0007](0007-ptz-onvif-fire-and-forget.md) | PTZ over ONVIF, fire-and-forget |
| [0008](0008-reboot-and-two-way-audio-live-in-vendor-p2p.md) | Reboot and two-way audio outside standard ONVIF; LAN RTSP talkback recovered |
| [0009](0009-storage-policy-never-delete-plus-retention.md) | Storage policy: never-delete monitor + opt-in retention |
| [0010](0010-rekey-to-authoritative-mac-and-backfill.md) | Re-key to authoritative MAC + capability backfill |
| [0011](0011-listen-in-audio-via-h264-web-variant.md) | Listen-in audio via the H.264 `_web` variant |
| [0012](0012-digital-zoom.md) | Digital zoom (no optical zoom to drive) |
| [0013](0013-browser-playable-recordings-transcode-cache.md) | Browser-playable recordings via a bounded HEVC→H.264 cache |
| [0014](0014-dashboard-i18n-no-build-step.md) | Dashboard i18n with no build step |
| [0015](0015-content-addressed-build-id.md) | Content-addressed build identity and automatic cache busting |
| [0016](0016-utc-recording-layout.md) | Recording paths and index timestamps are always UTC |
| [0017](0017-localhost-only-factory-provisioning.md) | Superseded: factory provisioning as a localhost-only boundary |
| [0018](0018-semantic-frontend-modules.md) | Semantic native ES modules with a thin orchestration entrypoint |
| [0019](0019-progressive-first-recording-playback.md) | Superseded: progressive first playback while the full transcode is cached |
| [0020](0020-lan-dashboard-and-bluetooth-onboarding.md) | LAN dashboard with trusted-LAN provisioning and homologated BLE onboarding |
| [0021](0021-seekable-first-recording-playback.md) | Seekable-first recording playback with asynchronous shared preparation |
| [0022](0022-resilient-recording-directory-rollover.md) | Resilient recording directory rollover and isolated maintenance failures |
| [0023](0023-opaque-driver-independent-camera-id.md) | Opaque public camera identity with backend driver-native mapping |
| [0024](0024-driver-owned-controls-and-vendor-packages.md) | Driver-owned semantic controls and vertical vendor packages |
| [0025](0025-opaque-media-and-recorder-runtime-identity.md) | Opaque media stream and recorder runtime identity |
| [0026](0026-opaque-recording-archive-identity.md) | Opaque recording archive identity with safe legacy backfill |
| [0027](0027-camera-id-primary-registry.md) | `camera_id` registry primary key with optional native MAC |
| [0028](0028-semantic-api-and-driver-onboarding.md) | Semantic API routers and driver-owned factory onboarding port |

The load-bearing decisions are now captured as ADRs. What remains in `DECISIONS.md` is journal:
status/planning sections (§5–§7), assorted UX tweaks and bugfix notes (e.g. persistent players §12,
PTZ latency detail, add/delete-under-compose fix §26) — kept as the historical narrative, not
promoted to ADRs.

## Current implementation and validation notes

These documents complement the ADRs. They distinguish shipped behavior, measurements,
hardware/browser validation and pending work; dates/build IDs are checkpoints, not manual versions.

| Topic | Evidence / scope |
| --- | --- |
| [Native archive playback](recordings-native-playback.md) | HEVC capability hints, one-shot fallback, original delivery, HTTP measurements and capped Chromium smoke test; native HEVC/mobile validation pending |
| [Recording player lifecycle](recordings-playback-lifecycle.md) | Selection ownership, cancellation, native controls, removed duplicate button and shared encoder budget |
| [Playback measurements](recordings-playback-measurements.md) | Cold/cached conversion, codec cache, Range/auth checks, deployment and scoped preparation logs |
| [Settings plan](settings-dashboard-plan.md) | Current env-only behavior, service snapshots, future authorization/persistence requirements |
| [Settings inventory](settings-inventory.json) | 42 classified fields; names only, CI checks completeness; not a runtime/API allowlist |
| [Session principal](session-principal.md) | Primary-only management dependency, exact legacy-cookie migration and pending security/deployment boundaries |
| [Temporary keys](temporary-access-keys.md) | Lifecycle and primary-only management API tested; API deployment pending, temporary login/UI disabled |
| [Open-session channels](session-channels.md) | Periodic socket revalidation and cancellation cleanup; not deployed, independent WebRTC/download invalidation pending |
| [Staged temporary sessions](temporary-sessions.md) | Fresh key validity and transitional default-deny permissions; public login disabled |
| [Temporary live media](temporary-live-media.md) | Registered-source MSE bridge, revocation and protocol restrictions; not deployed |
| [Login pacing](login-abuse-protection.md) | Fixed-memory origin quota and proxy identity boundary; backend deployment pending |
| [Login request boundaries](login-request-boundaries.md) | Body/time/concurrency limits, credential redaction, transport cookie flags and remaining origin/proxy review |
| [Browser origin policy](browser-origin-policy.md) | Shared origin checks, canonical public origin, HTTPS proxy cookies and migration limitations; not deployed |
| [Public file / route audit](public-file-audit.md) | Public asset allowlist, MP4 archive boundary, build-context exclusions and outstanding GET work; not deployed |
| [Recording GET boundary](recording-get-boundary.md) | Only explicit POST initiates HTTP preparation; legacy client migration, no deployment |
| [Public control/account errors](public-control-errors.md) | Fixed safe error messages and selected log hardening; query credentials and broader validation audit remain |
| [Discovery credential body](discovery-credential-body.md) | JSON-only scan credentials, bounded/redacted parsing and query-free bundled access logs; not deployed |
| [Sensitive validation](sensitive-validation.md) | Generic HTTP schema errors without rejected credentials or validator context; not deployed |
| [Provisioning public errors](provisioning-public-errors.md) | Safe errors, bounded BLE replies and strict enrollment status; remaining payload/log and firmware acceptance tracked |
| [Driver response/log audit](driver-response-log-audit.md) | Diagnostic/completion contracts, proxy/AAC fixes and privileged raw-read boundary |
| [Private media config](private-media-config.md) | Owner-only generated credential config preserving Docker file-bind identity; deployment/ownership caveats |
| [Safe stream diagnostics](safe-stream-diagnostics.md) | One-shot/recurring metadata-only collection, strict browser telemetry, no direct camera probes |
| [Dashboard session watch](dashboard-session-watch.md) | UI invalidation polling, stale-response guards and audio cleanup; end-to-end validation pending |
| [Revocable recording delivery](recording-session-delivery.md) | Temporary file/download cancellation with Range support; not deployed, proxy/browser validation pending |
| [Runtime settings](runtime-settings.md) | Deployed two-field persistence/API/UI, revision conflicts and next-operation semantics |
| [Driver package audit](driver-package-audit.md) | Discovery-only vs implemented driver support and vendor-package boundaries |

For user-facing instructions see the [public documentation index](../README.md). Keep planned
settings, temporary keys and WAN-independent provisioning out of implemented-feature claims.

## Format

`NNNN-short-title.md` — **Status** (proposed/accepted/superseded) · **Date**, then **Context**
(the forces), **Decision** (what we chose), **Consequences** (results + what we rejected). Keep it
short; de-identify examples (`aa:bb:cc:dd:ee:ff`, `192.168.1.x`) — never real device data.
## Latest checkpoint

- [Delegated access activation (2026-09-27)](delegated-access-activation.md): optional
  expiration, explicit feature grants, owner-only modal, login and guarded transports.
  Source-tested and locally deployed as `b-a84862f46bfb`; real-browser acceptance remains pending.
- [Native TCP cleanup](native-tcp-cleanup-checkpoint.md): in-server camera-3 validation
  decoded 55 HD frames with positive CLOSE/B9 receipts. Restored disabled; all three
  recorders grew afterward. Sustained/browser validation remains open.
