# Camera 3 control audit — 2026-09-13

## User confirmations and floodlight follow-up

After this audit, the user physically confirmed orientation, push-to-talk, recorded voice and
siren pulse. Night vision remains pending a nighttime test. Floodlight was physically NOK.
PTZ, relative speaker loudness, schedule enforcement and each selected alarm sound have not
received separate physical confirmations; the original evidence table below is preserved.

The floodlight driver reused the preflight BA receipt sequence for the subsequent B9 write:
preflight at N sent a receipt at N+1, then ON also used N+1. Readbacks could overlap receipts
too. The proven RE harness uses separated sequence ranges. Production now reserves four IDs
per exchange (one request plus up to three receipt offsets), including 32-bit wraparound.
It does not change the type-11 payload, loosen reply validation or retry actuation.

Exact-camera host retest with the corrected code passed OFF→ON→OFF with write verification
and independent strictly correlated ON/OFF reads. This strongly supports duplicate sequence
handling as the failure mechanism; no broker-side trace is available to prove internal discard.
Physical confirmation of the corrected light is still needed. Regression tests reject overlap
between commands and possible receipt IDs, including wraparound.

Deployed HTTP retest at 16:37:11–16:37:19 UTC also passed OFF→ON→OFF, with both writes
returning 200/verified and independent strictly correlated GETs matching. Final light state OFF.
Build `b-9ec1ba7fa584`; only app recreated, go2rtc not restarted. Build was capped at 512 MiB
and one CPU. Full Python suite, separate Node DOM harness, ruff and mypy passed; frontend
layout-contract test was also run after addition. User physical light confirmation remains pending.

User authorized real tests of all camera controls. Only the dedicated camera 3 was targeted.
Scope: all eight advertised semantic controls, both advertised intercom modes and standard PTZ.
Not a certification for other units, models or firmware. Exact enrolled identity was checked
before each phase using correlated product/version reads. No bypass of driver capability gates.

Tests ran sequentially from 15:55–16:01 UTC (12:55–13:01 Brasília), using authenticated localhost
dashboard APIs, not the manufacturer's app. Independent readbacks were used where possible.
There was no browser automation; microphone capture, modal rendering and end-to-end live playback
were not tested. The user was asked to observe physical effects; no physical confirmation has
been received at the time of this report. HTTP success is not physical homologation.

| Control | Live test | Evidence / outcome |
|---|---|---|
| White light | Off → on → restore off | **Failed**: ON returned 502, missing write reply; subsequent strictly correlated GET still reported off. Restore/off and final GET confirmed off. Recovery did not make this unit's command work. |
| Orientation | Normal → inverted → normal | PUT 200 both ways, exact `videoParm.setVal.multiFlip` in independent correlated reads matched both states. Physical image rotation still needs visual confirmation. |
| Night vision | Automatic → daytime → automatic | PUT 200 both ways; exact `videoParm.setVal.nightViewMode` independently matched. Does not establish IR-cut/illumination behavior in darkness. Night/IR option is not advertised and was not forced. |
| Speaker volume | 100 → 50 → 100 | API readback matched both writes. Acoustic loudness comparison not measured. Intermediate 75 was not separately exercised. |
| Smart protection | Enabled → disabled → enabled | State reads confirmed disabled before schedule testing and enabled after restoration. Detection/automated alarm effectiveness not tested. |
| Weekly protection schedule | All-day/all-week → Sunday 12:00–12:01 → original | Schedule fields read back exactly, then restored before re-enabling protection. Actual time-based triggering was not observed. |
| Alarm sound selection | Zumbido 1 → Zumbido 2 → Zumbido 1 | Both selections PUT 200; independent correlated resource reads matched exact resource keys. Original restored. Selection test itself did not invoke playback of each sound. |
| Siren pulse | 2 seconds | PUT 200, verified response (driver confirms OFF at cleanup). Physical sound not confirmed. Longer 5/10-second variants not exercised unnecessarily. |
| Voice message | Existing normalized sample, 3 seconds | HTTP 200, 150/150 frames acknowledged, session completed and route released. Audible output/intelligibility not yet confirmed. |
| Push to talk | Same sample paced as 150 × 20 ms websocket frames | `ready` then `complete`, 150/150 acknowledged, session completed and route released. Exercises dashboard streaming endpoint, not browser mic capture. Physical sound not yet confirmed. |
| PTZ | One standard-driver step left/right/up/down, final STOP | All five requests HTTP 200. Opposite steps do not prove exact positional restoration; displacement and physical STOP require observation. Native experimental PTZ was not used. |

No reset, reboot, SD formatting, recording deletion, provisioning or network change. The camera
catalogue advertises reboot=false; SD UI remains unavailable, so neither was force-enabled or
treated as a supported active control. Player-local zoom/quality and server recording navigation
are outside this camera-actuation audit. No other cameras were used for experiments.

## Preservation and limits

Original mutable settings were restored and read back: light off, orientation normal, night mode
automatic, volume 100, protection enabled, original weekly schedule and original alarm sound.
Siren cleanup was confirmed by the driver. Both audio routes reported released. PTZ position is
not measurable through this audit and must not be claimed precisely restored.

No rebuild, restart, emulator or browser. Scripts were bounded to 256 MiB virtual address space,
30 CPU seconds and phase wall deadlines; one phase at a time. Both app/go2rtc remained running
with unchanged start times and OOMKilled=false. This is not a live-video latency measurement.

Ignored reproduction/evidence (contains local unit details; do not commit):

- `re/audit_camera3_controls.py`: baseline, light roundtrip, guarded schedule, short siren.
- `re/audit_camera3_settings.py`: one setting roundtrip with independent observation/restoration.
- `re/audit_camera3_audio_ptz.py`: bounded message/websocket sample and finite standard PTZ steps.
- `temp/camera3-control-audit-20260913.json`: timestamped semantic results; no credentials.

## Next actions

1. Treat white-light actuation as a reproducible defect, not a solved acknowledgement issue.
   Inspect the exact-unit type-11 request/response against known working capture before changing
   the payload; do not brute-force light modes or widen supported models.
2. Obtain physical confirmations before labeling any of these controls fully homologated.
   In particular, distinguish parameter persistence from IR behavior, audible output and motion.
3. If dashboard errors persist on the controls that passed the API tests, correlate the exact
   camera/control/time with the browser request and displayed error. This run did not reproduce
   a browser interaction and cannot rule out UI lifecycle/concurrency bugs.
