# Automatic camera address recovery

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
  Manual and automatic discovery share a non-overlapping scan gate (manual: 409 if
  busy; automatic: skip this cycle). Shutdown cancels queued network probes.

## Limits and follow-up

MAC is a practical LAN matching key, not cryptographic device authentication.
Wi-Fi/Ethernet MACs can differ; spoofed/changed MACs must not be silently rebound.
Without a known MAC this generic worker cannot recover identity: driver-specific
serial/device-ID discovery remains future work. ONVIF requiring authentication,
nonstandard identity ports, routed NAT and incorrect subnet selection may prevent
automatic matching. Configure explicit bounded LAN subnets when needed.

The trigger is missing media progress (also catches wrong-device authentication),
not just TCP connectivity. It does not audit an apparently healthy stream whose
old IP was reassigned to another device accepting the same credentials. Periodic
identity verification and targeted hot source replacement are separate follow-ups.

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
