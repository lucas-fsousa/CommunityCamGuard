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

**Still in `DECISIONS.md`, to migrate:** device control/PTZ (§10, §13, §30), listen-in audio (§11),
software reboot / vendor P2P (§14, §17), recordings browser + playback cache (§19, §21, §25),
retention (§22), re-keying + capability backfill (§31), i18n (§24), digital zoom (§32), and the
various UX/bugfix notes. Status/planning sections (§5–§7) are journal, not ADRs.

## Format

`NNNN-short-title.md` — **Status** (proposed/accepted/superseded) · **Date**, then **Context**
(the forces), **Decision** (what we chose), **Consequences** (results + what we rejected). Keep it
short; de-identify examples (`aa:bb:cc:dd:ee:ff`, `192.168.1.x`) — never real device data.
