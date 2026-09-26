# Driver response and logging audit

Checkpoint: 2026-09-26. Source only, not deployed. No actual cameras, accounts,
network scans, encoders, SDK binaries or production logs were exercised/read.

## Implemented

- Online status, inventory and route probes validate exact booleans, nonnegative
  bounded counters and optional signed/unsigned 32-bit codes. Invalid diagnostics
  return fixed 502/no-store errors; arbitrary provider attributes are not returned.
- Completion omits `camera.stream_path` (unused by this dashboard response consumer).
  External clients must accommodate its removal. Camera ID/name/IP remain public
  registry metadata. Media transport/codecs and stage names use reviewed vocabulary;
  unknown labels are empty/omitted rather than reflected. New drivers extend this
  public vocabulary explicitly. No feature decisions or RTSP storage are changed.
- Primary media proxy errors log exception class only, never source URLs/raw errors.
- AAC FFmpeg stderr now goes to DEVNULL; failure text is fixed. Constructing the
  error no longer reads a pipe. Codec arguments, PCM and audio protocol are unchanged.

## Inspected logging boundaries

| Area | Evidence / boundary |
|---|---|
| BLE | Public projections and metadata-only logging covered by the provisioning audit. |
| Capability refresh | Exception class only. |
| White light | Caller-owned stage and integer error or fixed `unknown`. |
| PTZ | Exception class names, timing and receipt metadata in inspected log calls. |
| Native AV | Caller-owned stage/outcome, exception class and cleanup/timing flags. |
| Account storage | Fixed invalid-account warning. |
| RTSP completion | ffprobe stderr already DEVNULL. |
| Runtime native code | AMR loads its audio codec; P2P is repository Python. No vendor SDK binary was launched. Native codec stderr is outside Python log filtering. |
| External sinks | go2rtc, recorder FFmpeg, reverse proxies and custom launchers need separate review; historical logs were not rewritten. |

This bounded source review is not an end-to-end secrecy claim.

## Deliberate operator diagnostic

`/provisioning/privileged/p2p-property-read` returns camera-owned JSON for
driver-allowlisted read-only paths. It is authenticated trusted-LAN only, without
the remote BLE exception; temporary sessions are not authorized. The path allowlist
restricts operations, not the privacy of fields within returned values. This is not
a public/guest-safe capability catalogue.

Do not replace this with a generic secret-key denylist or silently remove useful
diagnostic fields. A sanitized public replacement requires driver-owned per-property
schemas and compatibility fixtures. The driver control catalogue remains the UI
feature contract. No diagnostic feature was removed by this audit.

## Verification / remaining gates

148 focused tests passed with synthetic driver values, malformed diagnostics,
completion URLs/stage names, proxy failures and AAC error construction. Ruff and
mypy passed (218 source files). Serial 512 MiB / 75% CPU caps: peak 120.1 MiB, no swap.

Before deployment: external-client completion compatibility, alternate BLE firmware
layouts and controlled browser/proxy acceptance. Public temporary login stays
disabled. Broader remaining work: per-property public schemas, camera catalogue
stream-path/capability privacy and external media-process logs.
