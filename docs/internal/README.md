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
| [0002](0002-mac-keyed-camera-identity.md) | Cameras identified by MAC, read from ONVIF (not ARP) |
| [0003](0003-layered-gentle-discovery.md) | Layered, gentle camera discovery |
| [0004](0004-crash-safe-fragmented-mp4-recording.md) | Crash-safe recording via fragmented MP4 |
| [0005](0005-live-view-transcode-and-codec-ceiling.md) | Live view: go2rtc, mandatory transcode, codec/CPU ceiling |
| [0006](0006-bounded-ffmpeg-oom-containment.md) | Bounded ffmpeg + cgroup limits to contain OOM |
| [0007](0007-ptz-onvif-fire-and-forget.md) | PTZ over ONVIF, fire-and-forget |
| [0008](0008-reboot-and-two-way-audio-live-in-vendor-p2p.md) | Reboot & two-way audio live in the vendor P2P channel |
| [0009](0009-storage-policy-never-delete-plus-retention.md) | Storage policy: never-delete monitor + opt-in retention |
| [0010](0010-rekey-to-authoritative-mac-and-backfill.md) | Re-key to authoritative MAC + capability backfill |
| [0011](0011-listen-in-audio-via-h264-web-variant.md) | Listen-in audio via the H.264 `_web` variant |
| [0012](0012-digital-zoom.md) | Digital zoom (no optical zoom to drive) |
| [0013](0013-browser-playable-recordings-transcode-cache.md) | Browser-playable recordings via a bounded HEVC→H.264 cache |
| [0014](0014-dashboard-i18n-no-build-step.md) | Dashboard i18n with no build step |

The load-bearing decisions are now captured as ADRs. What remains in `DECISIONS.md` is journal:
status/planning sections (§5–§7), assorted UX tweaks and bugfix notes (e.g. persistent players §12,
PTZ latency detail, add/delete-under-compose fix §26) — kept as the historical narrative, not
promoted to ADRs.

## Format

`NNNN-short-title.md` — **Status** (proposed/accepted/superseded) · **Date**, then **Context**
(the forces), **Decision** (what we chose), **Consequences** (results + what we rejected). Keep it
short; de-identify examples (`aa:bb:cc:dd:ee:ff`, `192.168.1.x`) — never real device data.
