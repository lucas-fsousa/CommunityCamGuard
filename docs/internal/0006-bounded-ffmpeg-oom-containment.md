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

## Consequences

- A misbehaving recorder can no longer take down the host — it fails contained and the supervisor
  recovers it. Verified: the timestamp warnings stopped, per-ffmpeg RSS stays in the tens of MB, and
  the kernel stops logging global OOM.
- The root cause was the **camera's broken bitstream timing**, not the recorder design; the fix
  stamps packets on arrival rather than trusting the camera's PTS.
