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

**Still in `DECISIONS.md`, to migrate:** listen-in audio (§11), browser-playable recordings +
playback cache (§19, §21, §25), i18n (§24), digital zoom (§32), persistent players (§12), and the
various UX/bugfix notes. Status/planning sections (§5–§7) are journal, not ADRs.

## Format

`NNNN-short-title.md` — **Status** (proposed/accepted/superseded) · **Date**, then **Context**
(the forces), **Decision** (what we chose), **Consequences** (results + what we rejected). Keep it
short; de-identify examples (`aa:bb:cc:dd:ee:ff`, `192.168.1.x`) — never real device data.
