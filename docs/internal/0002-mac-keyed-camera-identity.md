# 0002 — Cameras are identified by MAC, read from ONVIF (not ARP)

**Status:** superseded by [0023](0023-opaque-driver-independent-camera-id.md) and
[0027](0027-camera-id-primary-registry.md); retained for MAC discovery · **Date:** 2026-07-28

## Context

A camera's IP is a DHCP lease and changes; its **MAC** is stable. The original conclusion was that
the registry must be keyed by it. ADR 0027 later replaces that storage assumption with `camera_id`
while retaining the discovery facts below.

The MAC first came only from `/proc/net/arp` (the kernel ARP cache), which **only works on the same
L2 segment**. That ties stable identity to a specific network setup (e.g. Windows WSL2 in `mirrored`
mode); a routed or containerised deployment would have **no MAC → no stable identity**.

## Decision

Read the MAC from the camera itself over ONVIF: `control/device.py::mac_address(ip)` posts a no-auth
`GetNetworkInterfaces` to the device service and parses `<HwAddress>`, normalised to lower-case
`aa:bb:cc:dd:ee:ff` (`_normalize_mac` accepts dash/colon/bare-hex forms; rejects non-12-hex and
all-zero; takes the first interface with a real address).

Discovery prefers this **ONVIF MAC** over the ARP value and falls back to ARP only when ONVIF reports
none. The read happens on the port that already answered `GetDeviceInformation`, so it costs one
extra SOAP call — no new port probing, no new latency.

## Consequences

- Identity is **authoritative and ARP-independent** — it works off-LAN and containerised, not just
  on a mirrored/same-segment host. ARP stays a best-effort fallback.
- The MAC is read credential-free (same no-auth device service used for identification), so it adds
  no auth requirement to discovery.
- Follow-up (see ADR on re-keying): using the authoritative MAC to *re-key* an already-registered
  camera and migrate its recordings.
