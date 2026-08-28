"""Tests for the active-scan discovery helpers (backend/app/discovery/active_scan.py). Sockets,
the ARP file, the RTSP session and the driver path list are stubbed, so these run offline.
"""
from unittest.mock import mock_open

from backend.app import drivers
from backend.app.control import device, media
from backend.app.discovery import active_scan
from backend.app.discovery.active_scan import RtspStream, ScannedHost

# --- ScannedHost properties ---------------------------------------------------------

def test_has_rtsp_true_only_when_an_rtsp_port_is_open():
    assert ScannedHost("10.0.0.1", open_ports=[554, 5000]).has_rtsp is True
    assert ScannedHost("10.0.0.1", open_ports=[5000, 50000]).has_rtsp is False


def test_working_streams_keeps_only_status_200():
    host = ScannedHost("10.0.0.1", streams=[
        RtspStream("rtsp://a/1", 200),
        RtspStream("rtsp://a/2", 401, needs_auth=True),
        RtspStream("rtsp://a/3", 404),
    ])
    assert [s.url for s in host.working_streams] == ["rtsp://a/1"]


# --- _hosts (CIDR expansion) --------------------------------------------------------

def test_hosts_expands_cidr_and_skips_invalid():
    hosts = active_scan._hosts(["192.168.1.0/30", "not-a-cidr"])
    assert hosts == ["192.168.1.1", "192.168.1.2"]     # /30 usable hosts; bad CIDR ignored


# --- enumerate_ports (mock the TCP connect) -----------------------------------------

def test_enumerate_ports_returns_sorted_open_ports(monkeypatch):
    open_set = {554, 5000}
    monkeypatch.setattr(active_scan, "_port_open", lambda ip, port, timeout: port in open_set)
    assert active_scan.enumerate_ports("10.0.0.1", ports=(5000, 554, 80), workers=4) == [554, 5000]


# --- _mac_for (mock /proc/net/arp) --------------------------------------------------

_ARP = ("IP address       HW type   Flags   HW address          Mask  Device\n"
        "10.0.0.9         0x1       0x2     AA:BB:CC:DD:EE:09   *     eth0\n"
        "10.0.0.8         0x1       0x0     00:00:00:00:00:00   *     eth0\n")


def test_mac_for_reads_and_normalises(monkeypatch):
    monkeypatch.setattr(active_scan, "open", mock_open(read_data=_ARP), raising=False)
    assert active_scan._mac_for("10.0.0.9") == "aa:bb:cc:dd:ee:09"


def test_mac_for_skips_incomplete_zero_entry(monkeypatch):
    monkeypatch.setattr(active_scan, "open", mock_open(read_data=_ARP), raising=False)
    assert active_scan._mac_for("10.0.0.8") is None       # all-zero MAC = not resolved


def test_mac_for_none_when_absent(monkeypatch):
    monkeypatch.setattr(active_scan, "open", mock_open(read_data=_ARP), raising=False)
    assert active_scan._mac_for("10.0.0.254") is None


def test_mac_for_swallows_missing_file(monkeypatch):
    def boom(*a, **k):
        raise OSError("no /proc on this OS")
    monkeypatch.setattr(active_scan, "open", boom, raising=False)
    assert active_scan._mac_for("10.0.0.9") is None       # e.g. macOS/Windows — no crash


# --- _probe_rtsp (mock the RTSP session + driver paths) -----------------------------

class FakeSession:
    def __init__(self, ip, responses):
        self.ip = ip
        self.base = f"rtsp://{ip}:554"
        self._responses = list(responses)
        self.requests = []

    def request(self, method, uri, **kw):
        self.requests.append((method, uri, kw))
        return self._responses.pop(0) if self._responses else None


def test_probe_rtsp_returns_empty_when_not_rtsp(monkeypatch):
    monkeypatch.setattr(active_scan.rtsp, "parse_status", lambda r: 0)   # OPTIONS didn't answer
    sess = FakeSession("10.0.0.9", ["OPTS"])
    assert active_scan._probe_rtsp(sess, 554, "admin", "pw") == []


def test_probe_rtsp_collects_200_and_401_paths(monkeypatch):
    monkeypatch.setattr(drivers, "rtsp_paths", lambda u, p: ["/onvif1", "/onvif2"])
    # OPTIONS ok; /onvif1 -> 200; /onvif2 -> 401 (real path, needs creds)
    monkeypatch.setattr(active_scan.rtsp, "parse_status",
                        lambda r: {"OPTS": 200, "D200": 200, "D401": 401}[r])
    sess = FakeSession("10.0.0.9", ["OPTS", "D200", "D401"])
    streams = active_scan._probe_rtsp(sess, 554, "", "")
    assert streams[0].status == 200 and streams[0].url == "rtsp://10.0.0.9:554/onvif1"
    assert streams[1].status == 401 and streams[1].needs_auth is True


def test_probe_rtsp_retries_401_with_auth(monkeypatch):
    monkeypatch.setattr(drivers, "rtsp_paths", lambda u, p: ["/onvif1"])
    monkeypatch.setattr(active_scan.rtsp, "auth_header", lambda *a, **k: "Digest x")
    monkeypatch.setattr(active_scan.rtsp, "parse_status",
                        lambda r: {"OPTS": 200, "D401": 401, "D200": 200}[r])
    sess = FakeSession("10.0.0.9", ["OPTS", "D401", "D200"])   # first DESCRIBE 401, authed one 200
    streams = active_scan._probe_rtsp(sess, 554, "admin", "pw")
    assert len(streams) == 1 and streams[0].status == 200
    assert "admin:pw@10.0.0.9" in streams[0].url
    assert any(kw.get("auth") for _, _, kw in sess.requests)   # the authed retry happened


# --- _identify (mock the no-auth ONVIF toolbox) -------------------------------------

def test_identify_fills_vendor_model_paths_and_onvif_mac(monkeypatch):
    host = ScannedHost("10.0.0.9", open_ports=[554, 5000])   # 554 is RTSP -> skipped for SOAP
    monkeypatch.setattr(device, "info", lambda ip, port, timeout:
                        {"manufacturer": "Yoosee", "model": "IPC", "firmware": "1.0"} if port == 5000 else None)
    monkeypatch.setattr(media, "stream_paths", lambda ip, port, timeout: ["/onvif1", "/onvif2"])
    monkeypatch.setattr(device, "mac_address", lambda ip, port, timeout: "aa:bb:cc:dd:ee:09")
    active_scan._identify(host)
    assert host.vendor == "Yoosee" and host.model == "IPC" and host.firmware == "1.0"
    assert host.stream_paths == ["/onvif1", "/onvif2"]
    assert host.mac == "aa:bb:cc:dd:ee:09"       # ONVIF MAC preferred
    assert host.driver == "yoosee"               # detected from the vendor string


def test_identify_falls_back_to_port_fingerprint_when_no_onvif(monkeypatch):
    host = ScannedHost("10.0.0.9", open_ports=[554, 50000])
    monkeypatch.setattr(device, "info", lambda ip, port, timeout: None)   # nothing identifies
    active_scan._identify(host)
    assert host.model == "" and host.vendor == ""
    assert host.driver == "generic"              # still addable via the generic fallback


def test_identify_swallows_onvif_errors(monkeypatch):
    host = ScannedHost("10.0.0.9", open_ports=[5000])
    def boom(*a, **k):
        raise OSError("timeout")
    monkeypatch.setattr(device, "info", boom)
    active_scan._identify(host)                   # must not raise
    assert host.driver == "generic"


# --- _probe_host + scan (orchestration) ---------------------------------------------

def test_probe_host_merges_ports_reads_arp_and_identifies(monkeypatch):
    monkeypatch.setattr(active_scan, "enumerate_ports", lambda ip: [5000, 50000])
    monkeypatch.setattr(active_scan, "_mac_for", lambda ip: "aa:bb:cc:dd:ee:09")
    monkeypatch.setattr(active_scan, "_identify", lambda host: setattr(host, "driver", "yoosee"))
    monkeypatch.setattr(active_scan.rtsp, "RtspSession",
                        lambda *a, **k: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(active_scan, "_probe_rtsp",
                        lambda sess, port, u, p: [RtspStream("rtsp://10.0.0.9/onvif1", 200)])
    host = active_scan._probe_host("10.0.0.9", [554], "admin", "pw", 3.0, 0.3)
    assert host.open_ports == [554, 5000, 50000]      # merged + sorted
    assert host.arp_mac == "aa:bb:cc:dd:ee:09"
    assert host.driver == "yoosee"                    # _identify ran
    assert host.working_streams and host.streams[0].status == 200


def test_scan_returns_empty_when_no_targets(monkeypatch):
    monkeypatch.setattr(active_scan, "_hosts", lambda subnets: [])
    assert active_scan.scan(subnets=["bad"]) == []


def test_scan_sweeps_then_probes_and_sorts(monkeypatch):
    monkeypatch.setattr(active_scan, "_hosts", lambda subnets: ["10.0.0.2", "10.0.0.1"])
    # only .1 answers, on 554
    monkeypatch.setattr(active_scan, "_port_open",
                        lambda ip, port, timeout: ip == "10.0.0.1" and port == 554)
    monkeypatch.setattr(active_scan, "_probe_host",
                        lambda ip, ports, *a, **k: ScannedHost(address=ip, open_ports=ports))
    out = active_scan.scan(subnets=["10.0.0.0/24"], ports=(554, 5000))
    assert [h.address for h in out] == ["10.0.0.1"]
    assert out[0].open_ports == [554]
