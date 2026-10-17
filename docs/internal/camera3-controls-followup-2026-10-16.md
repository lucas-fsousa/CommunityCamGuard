# Camera 3 control follow-up, 2026-10-16

Only camera 3 was targeted. The operator lifted the sound restriction and requested
50% speaker volume. Keep that requested volume; no changes to cameras 1/2.

## Results

- Volume setter reported failed write confirmation. An independent read returned
  raw 5 / 50%, error 0. No write replay; the requested state is confirmed.
- One strict two-second siren pulse returned ON/OFF application success and transport
  ACKs, then correlated final OFF state 1. Exchange 5.6s, peak 37.7 MiB/no swap.
  Operator confirmed sound and stopping. Five/ten seconds, the dashboard route,
  and resolution of the earlier intermittency are not proven. No profile registered.
- Optional full capability collection subsequently failed at heartbeat.
- Night-mode write returned application success, but the immediate read still showed
  automatic. Restore was sent but its response/read timed out. An independent fresh
  read confirmed original automatic mode 0. This single immediate check does not
  establish unsupported hardware. Night-mode physical validation remains incomplete.
- Dashboard floodlight test stopped at initial GET HTTP409, before any light write.
  No successful visual actuation is claimed.
- Service health OK; app/go2rtc running, no OOM flags. Individual test processes were
  capped at 192 MiB with zero swap and 50% CPU; no emulator or browser used.

## Concrete concurrency correction

Production capability collection did not reserve the shared broker control lock,
unlike control/PTZ operations. It now holds `own_control_channel` across authentication,
reads and socket cleanup, with no action retries or credential renewal added.
Competing-thread regression proves no socket opens while controls own the channel;
failure releases ownership. 99 focused tests passed (119.5 MiB peak/no swap).

This prevents an in-process overlap, but does not establish causation for the network
failures above. Locks are process-local: standalone RE scripts are not coordinated
with production maintenance and cannot certify production concurrency.

## Deployment and canonical floodlight acceptance

Commit `4f29242`, full CI `38986183758` passed. Built image `44713c754084` with
512 MiB RAM/no additional swap/50% CPU limits; only app recreated, go2rtc untouched.
After deployment, authenticated canonical floodlight GET returned200/off/verified.
The subsequent reversible API cycle returned200 and verified values for each step:
GET OFF → PUT ON → GET ON → PUT OFF → GET OFF, restored true. Total process20.516s,
47.9 MiB/no swap. The three-second pause followed the ON read; it is not a measurement
of actual light-on duration. Operator visual confirmation requested, still pending.

This is a successful end-to-end API operation after deployment, not controlled proof
that serialization alone fixed prior intermittent errors: recreation also resets
runtime state. A later PTZ maintenance keepalive warning still occurred; do not claim
all session failures resolved. Next: visual confirmation and longer serialized-control
observation, then night-mode validation without bypassing runtime capability guards.
