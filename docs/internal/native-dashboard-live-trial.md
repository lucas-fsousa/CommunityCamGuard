# Camera3 native dashboard trial — 2026-10-25

Status: real video reached the existing authenticated dashboard player; a900s
continuous trial completed. This is no longer only synthetic-MP4 integration.
It is **not** permanent production rollout, full native A/V recording, comparative
RTSP latency acceptance, mobile acceptance or a claim of support for all units.

The owner explicitly authorized temporary interruption of the shared hub, including
all three views/recorders, for this test. No reset/reboot, sound, light, PTZ or camera
configuration command was sent. Existing original containers/config were restored.

## Executed chain

The host-only `scripts/native_dashboard_trial.py` preserves the original hub config
and snapshots the encrypted SQLite database into a private temporary directory.
It resolves the actual named containers once and verifies private PID namespaces.
After stopping the original app/hub, fresh Docker inspection must confirm the same
IDs/images, not running/restarting, and PID0. This closes the old namespace, not
merely its stream map. The app container receives no Docker daemon socket.

The new `retire_legacy` operator boundary holds consumers, commits the transition
and invokes this physical-close verifier before issuing an authentic single-use
receipt. Native activation then uses the existing runtime/driver source/MP4 fanout
and existing `/api/go2rtc/ws` player alias. The hub restarts with camera3 omitted;
the two other cameras keep ordinary RTSP and recording. Native camera3 recording
stays held because this first delivery is video-only.

The trial source is explicitly constructed for the enrolled test unit, as a
homologation experiment. No fabricated `native_video` proof, brand-wide rollout,
public activation endpoint or manufacturer-app gateway was installed.

Uvicorn handles TERM/INT without bypassing the outer rollback. systemd ExecStopPost
also invokes rollback after process exit. Normal restoration is idempotent and
does not merge experimental policies/profiles back into the primary database.
Controls writes are blocked during the view-only trial to avoid accidental actions.

Host recorder permissions required a temporary owner change on only the other
two cameras' current/next UTC-hour directories (not files or recursive trees).
Original UID/GID are recorded before the change and restored at teardown using
network-disabled32MiB helper containers with only CHOWN capability and those
directories mounted. All final ownership/restoration checks passed. New recordings
remain ordinary files; existing footage was not deleted or rewritten.

## Actual failures corrected, not counted as success

1. TrialA stopped before readiness. Replaying real recorded HEVC reproduced a
   combined HEVC/x264 allocation failure at the512MiB **virtual address** limit,
   despite107.3MiB RSS. Output encoding now has a1GiB AS allowance, while decode-only
   keeps512MiB. Hard cgroup RSS limits and zero swap remain. The same1080p encode
   passed at108.1MiB RSS; incremental output at119.6MiB. Resolution was not reduced.
2. TrialsB/C exposed repeating buffer starvation: nominal/SPS15fps did not reflect
   live input timing. Live mode now stamps input from arrival wall-clock, emits VFR,
   flushes packets and uses shorter GOPs. It does not force nominal FPS or infer raw
   camera timestamp units/UTC. File replay at10fps with nominal15 yielded~10.17fps
   output at1920x1080. Full native audio/video timestamp mapping remains stage2.
3. The first sustained trials failed near260s with SQLite CANTOPEN. Procfs showed
   a1024 soft FD limit; descriptors reached967 at240s. High-frequency registry/P2P
   SQLite contexts now close explicitly rather than waiting for GC. A regression
   disables GC and repeats100 status cycles without descriptor growth.
4. Host-dev recorder access to root-owned hour directories initially failed; the
   bounded, reversible directory permissions procedure above fixed this in later
   trials. Do not claim the earlier interrupted intervals were recorded.

## Final trialD evidence

- Local time10:30:02–10:45:06 (13:30:02–13:45:06 UTC),900s native source run.
- Ninety10s snapshots: native running throughout and **two other recorders at every
  sample**. FDs ranged36–305, finishing99 rather than exhausting1024.
- Native camera3 browser telemetry: one initial `waiting`, then `playing`; no later
  waiting/stalled/watchdog-recovery events in the received logs. Absence of events
  is not independent visual confirmation of every frame; that confirmation remains
  pending. No new local browser was launched.
- A real authenticated socket capture from the earlier trial decoded as H264
  Baseline1920x1080; thirty decoded frame hashes showed changing frames. Footage
  and credentials remain ignored/private, not repository fixtures.
- Trial service:768MiB hard cap, zero swap, one CPU quota; peak634.2MiB, CPU4m28s
  over15m16s. The separately capped hub is outside that service's memory measurement.
- Original app/hub running afterward; original config and directory ownership
  restored. Primary source policy/enrollment/profile rows were not changed.

Next delivery: listen-in audio and archived native recordings from the **same
capture**, then controls coexistence/recovery. No parallel RTSP recording connection
to camera3 will be introduced as a shortcut. Permanent driver admission still needs
the exact-unit reviewed record and current identity evidence.
