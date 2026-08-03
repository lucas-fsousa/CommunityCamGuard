"""ONVIF WS-Discovery over UDP multicast, implemented with the standard library only.

Cheap Chinese IP cameras that speak ONVIF answer a WS-Discovery ``Probe`` sent to the
multicast group 239.255.255.250:3702. We parse the ``ProbeMatch`` replies to learn each
camera's ONVIF service URL (XAddrs) and its advertised scopes (name, hardware, location).

No third-party dependency is used here on purpose: ONVIF/zeep wheels are still flaky on
very new Python versions, and this keeps the "works everywhere" promise of the project.

NOTE: WS-Discovery is multicast. From inside WSL this only works once the distro runs in
``networkingMode=mirrored`` (see README). Under the default NAT networking the probe never
reaches the LAN, so this returns an empty list.
"""
from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

WS_DISCOVERY_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

_PROBE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
  xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
  xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
  xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{message_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""


@dataclass
class DiscoveredDevice:
    """A device that answered WS-Discovery.

    ``xaddrs`` are the ONVIF service endpoints; the first is normally used for later
    ONVIF SOAP calls (GetCapabilities, GetStreamUri, ...).
    """

    address: str  # source IP that replied
    xaddrs: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    types: str = ""

    @property
    def name(self) -> str | None:
        return self._scope_value("name")

    @property
    def hardware(self) -> str | None:
        return self._scope_value("hardware")

    def _scope_value(self, key: str) -> str | None:
        prefix = f"onvif://www.onvif.org/{key}/"
        for scope in self.scopes:
            if scope.startswith(prefix):
                return scope[len(prefix):].replace("%20", " ")
        return None


def _local_ipv4() -> str:
    """Best-effort primary local IPv4 (the interface used for the default route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is actually sent; this just picks the outgoing interface.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_probe_match(data: bytes, source_ip: str) -> DiscoveredDevice | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    device = DiscoveredDevice(address=source_ip)
    found = False
    for el in root.iter():
        tag = _strip_ns(el.tag)
        text = (el.text or "").strip()
        if tag == "XAddrs" and text:
            device.xaddrs = text.split()
            found = True
        elif tag == "Scopes" and text:
            device.scopes = text.split()
            found = True
        elif tag == "Types" and text:
            device.types = text
    return device if found else None


def discover(timeout: float = 4.0, retries: int = 2) -> list[DiscoveredDevice]:
    """Send WS-Discovery probes and collect unique ONVIF devices on the LAN."""
    local_ip = _local_ipv4()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    if local_ip != "0.0.0.0":
        # Force multicast egress on the LAN-facing interface.
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip)
        )
    sock.bind(("", 0))
    sock.settimeout(0.5)

    devices: dict[str, DiscoveredDevice] = {}
    try:
        for _ in range(retries):
            probe = _PROBE_TEMPLATE.format(message_id=uuid.uuid4()).encode("utf-8")
            sock.sendto(probe, (WS_DISCOVERY_ADDR, WS_DISCOVERY_PORT))

        deadline = timeout
        sock.settimeout(deadline)
        import time

        end = time.monotonic() + deadline
        while time.monotonic() < end:
            sock.settimeout(max(0.1, end - time.monotonic()))
            try:
                data, addr = sock.recvfrom(65535)
            except TimeoutError:
                break
            except OSError:
                break
            device = _parse_probe_match(data, addr[0])
            if device and device.xaddrs:
                key = device.xaddrs[0]
                if key not in devices:
                    devices[key] = device
    finally:
        sock.close()

    return list(devices.values())


if __name__ == "__main__":
    print(f"Local interface: {_local_ipv4()}")
    print("Probing for ONVIF cameras (WS-Discovery)...\n")
    results = discover()
    if not results:
        print("No devices answered. If you are on WSL, confirm networkingMode=mirrored.")
    for d in results:
        print(f"- {d.name or '(unnamed)'} @ {d.address}")
        print(f"    hardware: {d.hardware}")
        print(f"    onvif:    {d.xaddrs[0] if d.xaddrs else '-'}")
