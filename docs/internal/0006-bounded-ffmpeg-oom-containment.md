# 0006 — Bounded ffmpeg + cgroup limits to contain OOM

**Status:** accepted · **Date:** 2026-07-28

## Context

On a memory-tight host, a recorder `ffmpeg` copying the raw camera restream (`-c:v copy`) with broken
wall-clock timestamps couldn't interleave video+audio and **ballooned its muxing queue to ~2 GB of
RSS**. With no cgroup memory cap on the container, that tripped a **global kernel OOM**, which picked
victims *outside* the container (including the interactive session's `dbus-daemon`) — taking down the
host console. The recorder's supervisor then respawned ffmpeg every few seconds, repeating the cycle.

## Decision

Defence in depth:

1. **Cap the container** (`docker-compose.yml`: `mem_limit` + `memswap_limit` equal, so no swap
   thrash). A runaway is now a **cgroup OOM** — only the container's own process is killed, never a
   host process. `mem_limit` works under `network_mode: host`.
2. **Bound ffmpeg's buffers** and normalize timestamps so it can't balloon in the first place
   (`-max_muxing_queue_size`, `-rtbufsize`, and stamping packets on arrival with
   `-use_wallclock_as_timestamps 1 -fflags +genpts` — which also kept audio without a `_web`
   transcode).
3. **Harden the supervisor** (backoff on respawn) and **rotate the ffmpeg logs** (they had grown to
   tens of MB) so a crash loop can't fill the disk.
4. **Do not restart an unchanged external go2rtc at app startup.** Its in-process `/api/restart`
   can keep a producer used by an old consumer alive while the reconnecting dashboard creates an
   identical replacement. This was observed as four H.264 FFmpeg processes for three cameras and
   861 MiB in the 1 GiB media cgroup after an app-only rebuild. Startup now compares the complete
   generated config and merely checks health when unchanged; real registry/config changes and the
   explicit repair action still reload the media engine.

Live validation removed the pre-existing duplicate with one controlled media-engine restart and
then rebuilt/recreated only the app container. No second go2rtc startup occurred. The steady state
was exactly three H.264 FFmpeg processes for three cameras and about 520 MiB, down from four
processes/861 MiB; fresh recording segments appeared for all three cameras after reconnection.

## Consequences

- A misbehaving recorder can no longer take down the host — it fails contained and the supervisor
  recovers it. Verified: the timestamp warnings stopped, per-ffmpeg RSS stays in the tens of MB, and
  the kernel stops logging global OOM.
- The root cause was the **camera's broken bitstream timing**, not the recorder design; the fix
  stamps packets on arrival rather than trusting the camera's PTS.
- Rebuilding/restarting only the app container no longer creates duplicate preloaded transcoders
  in the long-lived external go2rtc container.
