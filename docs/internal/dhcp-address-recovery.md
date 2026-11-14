# Automatic camera address recovery

## Healthy-address identity audit — 2026-11-13

The worker now checks apparently healthy LEGACY RTSP addresses too, at most once
per five minutes. A matching neighbor MAC needs no network request. No ARP entry
(routed/NAT/unresolved) is unknown, not a mismatch. For a different neighbor, a
bounded credential-free lookup of that ONE registered IP (/32) first allows the
expected ONVIF identity to resolve Wi-Fi/Ethernet/proxy differences. Its existing
port/time limits and shared scan gate apply. No RTSP SETUP/PLAY, FFmpeg, stored
password or camera action is used by the audit.

A persistent mismatch enters the existing two-observation/full-scan/cooldown path.
The observed different MAC NEVER becomes the registered identity: only finding
the original exact MAC at another unique address allows the existing CAS update
and batched reload. A lookup that reconfirms the old address clears the hint and
does not reload. Names, IDs, credentials, capabilities and footage stay unchanged.
Native/owned sources are excluded; drivers keep ownership of their own transport.
Logs project only the public camera ID, not old/new IP or MAC.

This is best-effort LAN correction, NOT a cryptographic identity or pre-stream
security barrier. Wrong-device media can remain visible during detection/lookup.
MAC spoofing, absent neighbor data, unsupported ONVIF/interface identity and
misconfigured scan ranges remain limits. There is no camera-count limit; lookups
reuse the existing bounded scan worker/host budget. Driver-ID discovery and hot
per-source replacement remain separate follow-ups.

The healthy-wrong-camera case failed before the change and now passes.65focused
tests passed under160MiB/no swap/25% CPU, peak78.6MiB; Ruff/Mypy430 passed. Tests
cover healthy match/unknown, interface fallback, cooldown, cancellation, scan
exclusion, unchanged-address confirmation, native exclusion and preserved identity/
credentials. No real DHCP change, wrong-IP injection or camera command was used.

Read-only live probe with the built identity-audit image returned matching
identities for both installed legacy cameras; registry addresses/MACs and all
source revisions remained unchanged. The native source was not probed. This
confirms compatibility, not a real reassignment test.

The standard ONVIF device poster now bounds successful and HTTP-error bodies to
1MiB plus one overflow-detection byte. Oversized replies return unavailable,
never a truncated identity/success; responses close in both paths. This matters
when an old camera IP answers as some unrelated HTTP endpoint. Socket timeouts
remain inactivity timeouts, not a new absolute slow-body deadline.90focused
tests/Ruff/Mypy passed, with explicit oversized success/error guards, capped at
160MiB/no swap/25% CPU (peak109MiB). No large live response or load test was sent.

### Current deployment and actual delivery

Core identity audit32c3633 passed CI43516409858 (6263tests/1skip) and
Image43516409848. Final response-bound guard1385f2f passed CI43517280652
(6265tests/1skip plus frontend contracts) and Image43517280751.
Installed imagea95b0ad8419e/tag `wsl-dhcp-identity-final`, buildb-2085f459f9c3;
app start2026-11-13T22:14:24.103Z, OOM=false/restarts=0. Builds were capped at
384MiB/no swap/25% CPU from clean tracked-file contexts; private data/footage
were excluded. Only the app was replaced; the hub/other projects were not stopped.

One initial post-deployment probe failed its combined online/recording assertion
without recording which flag/unit was negative. The next separate status query
reported all three healthy, and inspected logs showed no DHCP update/worker error,
recorder exit or terminal native-source event in that interval. This does NOT
establish an actual recording interruption or its cause.

A repeated40s authenticated HD check delivered82fragments/28856472bytes, polling
per-camera status every5s with ZERO negative samples. Actual archive files grew
for all three; source revisions stayed unchanged during observation. Final state:
camera3 native/active288, garage/yard legacy RTSP/active22/22, all online/recording.
This verifies compatibility and delivery, not a deliberate real DHCP reassignment,
cryptographic identity guarantee or decoded-frame/mobile audit.

## Incident and cause — 2026-09-14

After a power outage/router restart, DHCP reassigned camera addresses. The API and
go2rtc both answered HTTP 200, with no OOM/restart loop, but all three sources and
recorders were offline. Old addresses produced timeout, unreachable-host and RTSP
authentication errors. The latter can mean the old IP now belongs to a different
camera, not necessarily that the registered password is wrong.

Previously `POST /api/discovery/scan` reconciled MAC/address in SQLite only. There
was no automatic recovery worker, and manual scanning did not resync live services.

## Implemented behavior

- With service autostart enabled, one background worker checks media progress every
  30 seconds, independently of dashboard polling. Two offline observations trigger
  a credential-free LAN lookup, with a five-minute minimum between scan starts.
- Discovery uses the configured `DISCOVERY_SCAN_SUBNETS`, otherwise the existing
  local `/24` detector. Rejects IPv6 or more than 512 total subnet addresses before
  allocating hosts. Four TCP/identity workers, 250 ms TCP timeout, 1 s ONVIF timeout.
- One RTSP-port TCP knock; no SETUP/PLAY or extra FFmpeg. Match the responding
  endpoint's ARP MAC, or read ONVIF `GetNetworkInterfaces` on common service ports.
  No stored camera passwords, controls, lights, sound or reboot commands are sent.
- Exact known MACs only; ambiguous duplicate MACs do not update anything. Unknown
  cameras are not registered. Public ID, driver, capabilities, password, name and
  recordings remain unchanged. SQLite compare-and-swap rejects a stale result if
  that camera was removed or its address/MAC changed while scanning.
- Changed addresses are persisted together with a sanitized log, then the existing
  media reload is invoked once per batch. This reload can briefly interrupt other
  viewers too; unchanged/unresolved scans do not reload. Failed application is
  retried on the next worker tick. A deliberate recorder pause is preserved.
- Manual dashboard scanning now also resyncs media when registered IPs changed.
  Before registry writes, the complete batch is checked for conflicting MAC/IP
  claims and competing ARP-to-ONVIF aliases. Ambiguous primary identities cannot
  update a saved camera or become new candidates; ambiguous registered ARP aliases
  cannot rekey a saved camera. Identical observations are
  processed once; unrelated unambiguous devices remain discoverable. Matching is
  case-insensitive and still only a LAN hint, not cryptographic authentication.
  The matcher lives in `discovery/identity_claims.py`, separate from persistence.
  Three regressions first reproduced wrong-address updates, unsafe alias rekeys
  and duplicate results. The final registry/discovery/reconciliation batch passed
  68 tests under 160 MiB/no swap/25% CPU (79.2 MiB peak), plus Ruff and mypy
  (91.9 MiB peak). These tests use temporary databases, not real camera changes.
  Native-only address updates skip the shared hub and legacy recorder reload:
  these routes do not embed the registry IP. This does not reconnect the native
  actor or clear an unrelated pending reload. Mixed, owned-RTSP and legacy-RTSP
  batches retain the conservative existing reload; ordinary camera CRUD is unchanged.
  Manual and automatic discovery share a non-overlapping scan gate (manual: 409 if
  busy; automatic: skip this cycle). Shutdown cancels queued network probes.

## Limits and follow-up

MAC is a practical LAN matching key, not cryptographic device authentication.
Wi-Fi/Ethernet MACs can differ; spoofed/changed MACs must not be silently rebound.
Without a known MAC this generic worker cannot recover identity: driver-specific
serial/device-ID discovery remains future work. ONVIF requiring authentication,
nonstandard identity ports, routed NAT and incorrect subnet selection may prevent
automatic matching. Configure explicit bounded LAN subnets when needed.

Missing media progress (also catches wrong-device authentication) and the periodic
healthy-address audit above trigger recovery. The audit relies on available LAN
identity hints; it cannot detect a reassigned endpoint that impersonates the same
MAC or offers no contradictory identity. Targeted hot replacement remains pending.

The pinned go2rtc 1.9.14 API is not a physical retirement receipt: PUT replaces a
stream reference and DELETE removes it without stopping the previous object;
PATCH updates the producer URL without closing its active connection. Therefore
an HTTP success alone must not authorize a replacement capture or prove that a
healthy wrong-device connection has ended. See the upstream
[stream API](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/internal/streams/api.go),
[stream registry](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/internal/streams/streams.go)
and [producer lifecycle](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/internal/streams/producer.go).
The native-only optimization deliberately does not claim to solve active RTSP
replacement. No additional producer, encoder, scan or camera control is introduced.

The 2026-11-13 regression batch covers empty/native-only/mixed/owned/legacy
updates, missing policy, policy-read failure, preserved pending work, unchanged
ordinary reconciliation and exact changed-ID forwarding from the manual scan.
All 54 focused recovery/reconciliation/SOAP tests passed with a 160 MiB memory
cap and no swap (70.6 MiB peak); Ruff and mypy also passed (114.9 MiB peak).

Deployment of code `9b7336c`: clean tracked-files Docker context (2.948 MB),
384 MiB/no swap/25% CPU build limit, image `69ea21bd8625`, build
`b-87e7476695c8`. CI `43521061781` passed 6274 tests/1 skip and every frontend
contract; image workflow `43521061801` passed. Only the app was recreated, at
2026-11-13T22:41:53.562Z; the shared hub retained its prior start time.

An authenticated 40-second native-HD MSE reader received 43 binary fragments /
8141299 bytes, with no negative online/recording samples and unchanged source
revisions (native 294, legacy 22/22). Original archive files grew for each of the
three cameras. Three FFmpeg processes remained; app memory sampled 214.5 MiB,
hub 7.754 MiB, OOM=false/restarts=0. No camera address was deliberately changed,
so this is deployment/delivery verification, not a real DHCP-change acceptance
test. No sound/light/motion/settings command or extra encoder was used.

Tests cover debounce/cooldown, unchanged/healthy cameras, duplicate identity,
ONVIF fallback, cancellation, address CAS/deletion, preserved credentials/ID,
reload retry, recorder pause, manual resync and scan exclusion.

## Deployment evidence

Build `b-5e3e1ce51f6d` deployed with a 512 MiB build memory limit. The bounded
read-only lookup found all three new addresses. Before applying them, a separate
ONVIF MAC read matched every saved identity and authenticated RTSP DESCRIBE returned
success for all three (no PLAY, camera reboot or control writes). Address CAS then
updated the registry, and app startup applied the changed media configuration.
Authenticated `/api/cameras` reported all three `online=true`, `recording=true`.

Python suite, nine recovery regression tests, Ruff, mypy (165 source files) and
Node camera-panel/PTZ contracts passed. No WSL OOM; no container OOM/restart loop.
The next natural DHCP change still needs end-to-end observation of the scheduled
worker: this incident was restored using the verified lookup results directly,
without deliberately causing another outage or falsifying database addresses.
