# Architecture Decision Records (ADRs)

The *why* behind non-obvious design choices — one decision per file, in **Context / Decision /
Consequences** form. Reference them from code as `docs/internal/NNNN-...md`.

These are being distilled from the historical [`../DECISIONS.md`](../DECISIONS.md) log (a single
numbered journal). The ADRs below are authoritative for the decisions they cover; `DECISIONS.md`
remains the fuller narrative record and the source for decisions not yet migrated.

## Index

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

## Format

`NNNN-short-title.md` — **Status** (proposed/accepted/superseded) · **Date**, then **Context**
(the forces), **Decision** (what we chose), **Consequences** (results + what we rejected). Keep it
short; de-identify examples (`aa:bb:cc:dd:ee:ff`, `192.168.1.x`) — never real device data.
