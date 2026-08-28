"""Active subnet scan fallback for cameras that ignore ONVIF WS-Discovery.

Many cheap "generic" IP cameras expose RTSP (and sometimes an HTTP/ONVIF service)
but never answer the multicast WS-Discovery ``Probe``. For those we sweep the local
subnet(s) looking for the well-known camera ports, then confirm any RTSP servers with
an actual ``OPTIONS``/``DESCRIBE`` handshake and try the common vendor stream paths.

Standard library only, on purpose (see ``ws_discovery`` for the rationale). RTSP is a
text protocol very close to HTTP, so we speak it directly over a TCP socket, including
Basic and Digest authentication for the ``DESCRIBE`` probe.

Like WS-Discovery this needs the WSL distro in ``networkingMode=mirrored`` to reach the
LAN; unlike WS-Discovery it does not need multicast, so it is the more robust option for
odd cameras.

**Gentleness is a hard requirement.** The target cameras run tiny embedded RTSP servers
that hang or drop off the network under connection pressure (see docs/DECISIONS.md). So
the scan is deliberately gentle: a cheap moderate-concurrency TCP port sweep first, then
RTSP probing that **reuses a single connection per camera** for OPTIONS + every DESCRIBE,
throttles between requests, and runs across only a couple of cameras at a time — never the
old fan-out of many short-lived sockets. We only ever OPTIONS/DESCRIBE (never SETUP), so
there is no media session to TEARDOWN; the socket is simply closed cleanly when done.
"""
from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import rtsp
from .ws_discovery import _local_ipv4

# Ports worth knocking on. RTSP first (that is what we can actually confirm), then the
# common HTTP/ONVIF and proprietary control ports used by Dahua/Hikvision/XMEye clones.
RTSP_PORTS = (554, 8554)
HTTP_PORTS = (80, 8000, 8080, 8899)
ONVIF_PORT = 5000                       # HiSilicon/Yoosee ONVIF service (answers no-auth)
PROPRIETARY_PORTS = (ONVIF_PORT, 50000, 34567, 37777)
# Ports that commonly host an HTTP/ONVIF device service, tried first when identifying a camera
# (a priority order over the open ports — not an assumption that ONVIF lives on any one of them).
_HTTP_LIKELY_PORTS = (80, 8000, 8080, 8899, 5000, 81, 8081, 2020, 8443, 443)
DEFAULT_PORTS = RTSP_PORTS + HTTP_PORTS + PROPRIETARY_PORTS

# Once a host answers on a camera port we enumerate a wider set on *that host only* — a
# curated list of ports embedded cameras are known to expose (control, HTTP alts, ONVIF,
# proprietary). This closes the gap where a fixed subnet-sweep list would miss a camera's
# real control ports (e.g. Yoosee's 5000/50000). It stays cheap: it is a per-host TCP
# connect sweep over a bounded list, run only for the handful of hosts already found.
WIDE_PORTS = tuple(sorted({
    *DEFAULT_PORTS,
    81, 82, 88, 443, 843, 1935, 2020, 3702, 5001, 7001, 7070, 8001, 8081, 8082,
    8090, 8091, 8181, 8188, 8443, 8555, 8600, 8888, 8900, 9000, 9001, 9090, 9999,
    49152, 49153, 50001,
}))

# Stream paths to try come from the camera ``drivers`` (union, most-common first).


@dataclass
class RtspStream:
    """One RTSP URL we probed, with the DESCRIBE status we got back.

    ``status`` 200 = usable as-is; 401 = the path/server is real but needs credentials;
    anything else is kept out of the results.
    """

    url: str
    status: int
    needs_auth: bool = False


@dataclass
class ScannedHost:
    """A host that answered on at least one interesting port.

    ``mac`` is the stable per-camera identity: unlike ``address`` (a DHCP lease that can
    change) the MAC is fixed in hardware. We prefer the camera's own MAC from ONVIF
    ``GetNetworkInterfaces`` (authoritative, ARP-independent — see ``_identify``); when that
    isn't available we fall back to the ARP table, which needs WSL ``mirrored`` (shared L2
    segment). The dashboard keys cameras by MAC and treats the IP as a mutable "last-seen
    address".

    ``arp_mac`` keeps the ARP-derived address even when ONVIF overrode ``mac``. The two can
    disagree (a camera registered before we read ONVIF, or a Wi-Fi/ethernet interface split),
    and the registry needs the old value to recognise an already-registered camera and *re-key*
    it rather than offer it as a new candidate (see ``registry.rekey_camera``).
    """

    address: str
    mac: str | None = None
    arp_mac: str | None = None
    open_ports: list[int] = field(default_factory=list)
    streams: list[RtspStream] = field(default_factory=list)
    # No-auth identification — filled across the open ports before any RTSP login (see _identify).
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    driver: str = ""
    stream_paths: list[str] = field(default_factory=list)

    @property
    def has_rtsp(self) -> bool:
        return any(p in RTSP_PORTS for p in self.open_ports)

    @property
    def working_streams(self) -> list[RtspStream]:
        """Streams that are directly usable (authenticated OK / no auth needed)."""
        return [s for s in self.streams if s.status == 200]


def _subnet_from_local() -> str | None:
    """Derive the local /24 (e.g. ``192.168.1.0/24``) from the LAN interface."""
    ip = _local_ipv4()
    if ip in ("0.0.0.0", ""):
        return None
    net = ipaddress.ip_network(f"{ip}/24", strict=False)
    return str(net)


def _hosts(subnets: list[str]) -> list[str]:
    hosts: list[str] = []
    for cidr in subnets:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        hosts.extend(str(h) for h in net.hosts())
    return hosts


def _port_open(ip: str, port: int, timeout: float) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((ip, port)) == 0


def enumerate_ports(ip: str, ports: tuple[int, ...] = WIDE_PORTS,
                    timeout: float = 0.6, workers: int = 16) -> list[int]:
    """Return the open ports on a single host, from a wider curated list.

    Used to deepen discovery once a camera IP is known: a plain TCP connect sweep (no
    protocol chatter), moderate concurrency, over a bounded port list — cheap and gentle
    enough for one host at a time.
    """
    def check(port: int) -> tuple[int, bool]:
        return port, _port_open(ip, port, timeout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return sorted(p for p, ok in pool.map(check, ports) if ok)


def _mac_for(ip: str) -> str | None:
    """Read the MAC for ``ip`` from the kernel ARP cache (populated once we connect).

    Only works on the same L2 segment (i.e. WSL ``mirrored``). Returns a normalised
    lower-case ``aa:bb:cc:dd:ee:ff`` or None if not resolved / incomplete.
    """
    try:
        with open("/proc/net/arp") as fh:
            for line in fh.readlines()[1:]:
                cols = line.split()
                if len(cols) >= 4 and cols[0] == ip:
                    mac = cols[3].lower()
                    if mac and mac != "00:00:00:00:00:00":
                        return mac
    except OSError:
        pass
    return None


# --- gentle RTSP probing (built on the rtsp client module) -------------------------

def _probe_rtsp(
    session: rtsp.RtspSession,
    port: int,
    username: str,
    password: str,
    *,
    driver_key: str | None = None,
    discovered_paths: list[str] | None = None,
) -> list[RtspStream]:
    """Confirm RTSP with one OPTIONS, then DESCRIBE the candidate paths on the same conn."""
    opts = session.request("OPTIONS", session.base + "/")
    if opts is None or rtsp.parse_status(opts) == 0:
        return []  # not actually speaking RTSP

    streams: list[RtspStream] = []
    from .. import drivers  # local import keeps the discovery<->drivers module load order simple
    for path in drivers.rtsp_paths_for(
        driver_key,
        username,
        password,
        discovered=discovered_paths or (),
    ):
        uri = session.base + path
        resp = session.request("DESCRIBE", uri, accept_sdp=True)
        if resp is None:
            break  # connection dropped — stop gently rather than reconnect-and-hammer
        status = rtsp.parse_status(resp)
        if status == 401 and username:
            resp = session.request(
                "DESCRIBE", uri, accept_sdp=True,
                auth=rtsp.auth_header(resp, "DESCRIBE", uri, username, password),
            )
            if resp is None:
                break
            status = rtsp.parse_status(resp)

        if status == 200:
            cred = f"{username}:{password}@" if username else ""
            streams.append(RtspStream(url=f"rtsp://{cred}{session.ip}:{port}{path}", status=200))
        elif status == 401:
            streams.append(RtspStream(url=uri, status=401, needs_auth=True))
        # 404 / 400 / other → wrong path, skip
    return streams


def _identify(host: ScannedHost) -> None:
    """Identify the camera (vendor/model/firmware + RTSP paths + driver) with **no login**.

    We do **not** assume which port hosts the identification service — different brands expose
    ONVIF on different ports (80 / 8000 / 8080 / 8899 / 5000 / ...). Instead we take the ports the
    port scan found **open** and try the no-auth ONVIF ``GetDeviceInformation`` across them
    (skipping pure-RTSP media ports, which don't speak SOAP), first answer wins; then read the
    media service's real RTSP paths on that same port. From the discovered vendor/model we pick
    the driver, and the driver's happy path takes over from there.

    Two gentle passes (a quick one, then a slower retry only if nothing answered) cover the cheap
    cams that reply to ONVIF intermittently under load. Fully best-effort: if nothing identifies,
    we still fall back to a driver detected from the open-port fingerprint, so the camera stays
    addable. Only *identification* is credential-free — the RTSP stream still needs the password.
    """
    from .. import drivers
    from ..control import device, media  # local import: control is a leaf toolbox (no cycle)
    # Candidates = every open port except pure-RTSP media ports (which can't answer SOAP). We
    # order HTTP/ONVIF-likely ports first so we usually succeed before spending a timeout on a
    # silent binary port (e.g. a P2P control channel that accepts TCP but never replies). This
    # is a priority, not an assumption — any open port is still tried if the likely ones miss.
    ports = [p for p in host.open_ports if p not in RTSP_PORTS]
    ordered = ([p for p in _HTTP_LIKELY_PORTS if p in ports]
               + [p for p in ports if p not in _HTTP_LIKELY_PORTS])
    # Generous timeout: a healthy camera answers in well under a second, but these cheap cams get
    # very slow under load (measured up to ~12s when heavily probed), and this is scan-time where
    # latency doesn't matter — better to wait than miss the identity. A non-ONVIF HTTP port errors
    # out fast (not a timeout); only a silent binary port would burn the full budget, which the
    # likely-port ordering above generally avoids reaching.
    timeout = 10.0
    for port in ordered:
        try:
            info = device.info(host.address, port=port, timeout=timeout)
        except OSError:
            info = None
        if info and info.get("model"):
            host.vendor = info.get("manufacturer", "") or host.vendor
            host.model = info.get("model", "")
            host.firmware = info.get("firmware", "")
            try:
                host.stream_paths = media.stream_paths(host.address, port=port, timeout=timeout)
            except OSError:
                host.stream_paths = []
            # Prefer the camera's own MAC (ONVIF GetNetworkInterfaces) over the ARP-derived one:
            # authoritative and ARP-independent, so identity holds without WSL mirrored mode.
            # ``arp_mac`` is left alone so the registry can still match — and re-key — a camera
            # that was registered under the ARP MAC before we could read the ONVIF one.
            try:
                onvif_mac = device.mac_address(host.address, port=port, timeout=timeout)
            except OSError:
                onvif_mac = None
            if onvif_mac:
                host.mac = onvif_mac
            break
    # Pick the driver from what we learned (vendor/model + open ports); generic if nothing claims it.
    ctx = drivers.DetectContext(
        vendor=host.vendor,
        model=host.model,
        firmware=host.firmware,
        open_ports=host.open_ports,
    )
    host.driver = drivers.detect(ctx).key


def _probe_host(ip: str, open_ports: list[int], username: str, password: str,
                rtsp_timeout: float, probe_delay: float,
                deep_ports: bool = True) -> ScannedHost:
    # Deepen the port picture for this now-known camera IP (fills in control ports the
    # fixed subnet-sweep list would miss, e.g. 5000/50000). Merge, don't replace.
    if deep_ports:
        open_ports = sorted(set(open_ports) | set(enumerate_ports(ip)))
    arp_mac = _mac_for(ip)
    host = ScannedHost(address=ip, mac=arp_mac, arp_mac=arp_mac, open_ports=open_ports)
    # Identify first so an installed driver contributes paths only to its own family. Unknown
    # cameras retain the broad compatibility union, but adding a driver no longer adds probes to
    # every already-identifiable camera on the network.
    _identify(host)
    for port in open_ports:
        if port not in RTSP_PORTS:
            continue
        try:
            session = rtsp.RtspSession(ip, port, rtsp_timeout, probe_delay)
        except OSError:
            continue
        try:
            host.streams.extend(
                _probe_rtsp(
                    session,
                    port,
                    username,
                    password,
                    driver_key=host.driver,
                    discovered_paths=host.stream_paths,
                )
            )
        finally:
            session.close()
    return host


def scan(subnets: list[str] | None = None, ports: tuple[int, ...] = DEFAULT_PORTS,
         username: str = "", password: str = "", connect_timeout: float = 0.5,
         rtsp_timeout: float = 3.0, probe_delay: float = 0.3,
         sweep_workers: int = 32, probe_workers: int = 2,
         deep_ports: bool = True) -> list[ScannedHost]:
    """Scan the given subnets (default: local /24) for reachable cameras — gently.

    Phase 1: a moderate-concurrency TCP port sweep (cheap, one connect per host/port).
    Phase 2: gentle RTSP probing of hosts with an RTSP port — one reused connection per
    camera, throttled, and only ``probe_workers`` cameras at a time.

    Returns hosts that had at least one interesting port open, with any RTSP stream paths
    that answered DESCRIBE (200 = usable, 401 = real but needs/rejected credentials).
    """
    if not subnets:
        local = _subnet_from_local()
        subnets = [local] if local else []
    targets = _hosts(subnets)
    if not targets:
        return []

    # Phase 1 — cheap port sweep.
    def sweep(ip: str) -> tuple[str, list[int]]:
        return ip, [p for p in ports if _port_open(ip, p, connect_timeout)]

    open_by_host: dict[str, list[int]] = {}
    with ThreadPoolExecutor(max_workers=sweep_workers) as pool:
        for ip, open_ports in pool.map(sweep, targets):
            if open_ports:
                open_by_host[ip] = open_ports

    # Phase 2 — gentle RTSP probe (few cameras at a time, one reused connection each).
    def probe(item: tuple[str, list[int]]) -> ScannedHost:
        ip, open_ports = item
        return _probe_host(ip, open_ports, username, password, rtsp_timeout, probe_delay,
                           deep_ports=deep_ports)

    results: list[ScannedHost] = []
    with ThreadPoolExecutor(max_workers=probe_workers) as pool:
        results.extend(pool.map(probe, open_by_host.items()))
    results.sort(key=lambda h: tuple(int(p) for p in h.address.split(".")))
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Active subnet scan for IP cameras.")
    parser.add_argument("--subnet", action="append", default=None,
                        help="CIDR to scan (repeatable). Default: local /24.")
    parser.add_argument("--user", default="", help="RTSP username for path probing.")
    parser.add_argument("--password", default="", help="RTSP password for path probing.")
    args = parser.parse_args()

    print(f"Local interface: {_local_ipv4()}")
    subnets = args.subnet or [_subnet_from_local()]
    print(f"Scanning: {', '.join(s for s in subnets if s)} ...\n")

    hosts = scan(subnets=args.subnet, username=args.user, password=args.password)
    if not hosts:
        print("No hosts answered on camera ports. Confirm networkingMode=mirrored.")
    for h in hosts:
        ident = f"  {h.vendor} {h.model} fw={h.firmware}".rstrip() if h.model else ""
        print(f"- {h.address}  mac={h.mac or '?'}  driver={h.driver or '?'}{ident}")
        print(f"    ports={h.open_ports}  onvif-paths={h.stream_paths or '-'}")
        for s in h.streams:
            tag = "OK" if s.status == 200 else f"auth-required ({s.status})"
            print(f"    [{tag}] {s.url}")
