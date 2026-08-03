"""Tests for ONVIF WS-Discovery (backend/app/discovery/ws_discovery.py). The UDP socket is faked,
so the probe/parse flow runs offline.
"""
from backend.app.discovery import ws_discovery as wsd

_PROBE_MATCH = b"""<?xml version="1.0"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
  xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <e:Body><d:ProbeMatches><d:ProbeMatch>
    <d:XAddrs>http://10.0.0.9/onvif/device_service http://10.0.0.9:8000/onvif</d:XAddrs>
    <d:Scopes>onvif://www.onvif.org/name/Front%20Door onvif://www.onvif.org/hardware/IPC</d:Scopes>
    <d:Types>dn:NetworkVideoTransmitter</d:Types>
  </d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>"""


# --- pure helpers -------------------------------------------------------------------

def test_strip_ns():
    assert wsd._strip_ns("{http://ns}XAddrs") == "XAddrs"
    assert wsd._strip_ns("Scopes") == "Scopes"


def test_scope_values_decode_and_default():
    dev = wsd.DiscoveredDevice(address="10.0.0.9", scopes=[
        "onvif://www.onvif.org/name/Front%20Door",
        "onvif://www.onvif.org/hardware/IPC",
    ])
    assert dev.name == "Front Door"          # %20 decoded
    assert dev.hardware == "IPC"
    assert wsd.DiscoveredDevice(address="x").name is None   # no scopes -> None


# --- _parse_probe_match -------------------------------------------------------------

def test_parse_probe_match_extracts_fields():
    dev = wsd._parse_probe_match(_PROBE_MATCH, "10.0.0.9")
    assert dev is not None
    assert dev.address == "10.0.0.9"
    assert dev.xaddrs == ["http://10.0.0.9/onvif/device_service", "http://10.0.0.9:8000/onvif"]
    assert dev.name == "Front Door" and dev.hardware == "IPC"
    assert dev.types == "dn:NetworkVideoTransmitter"


def test_parse_probe_match_none_on_bad_xml():
    assert wsd._parse_probe_match(b"<not-xml", "10.0.0.9") is None


def test_parse_probe_match_none_when_no_relevant_tags():
    assert wsd._parse_probe_match(b"<a><b>hi</b></a>", "10.0.0.9") is None


# --- _local_ipv4 --------------------------------------------------------------------

class FakeUDP:
    def __init__(self, *, sockname="10.0.0.2", recv=(), connect_fails=False):
        self._sockname = sockname
        self._recv = list(recv)
        self._connect_fails = connect_fails
        self.sent = []
        self.closed = False

    def setsockopt(self, *a): pass
    def settimeout(self, t): pass
    def bind(self, addr): pass
    def connect(self, addr):
        if self._connect_fails:
            raise OSError("unreachable")
    def getsockname(self): return (self._sockname, 0)
    def sendto(self, data, addr): self.sent.append((data, addr))
    def recvfrom(self, n):
        if self._recv:
            return self._recv.pop(0)
        raise TimeoutError
    def close(self): self.closed = True


def test_local_ipv4_returns_interface(monkeypatch):
    monkeypatch.setattr(wsd.socket, "socket", lambda *a, **k: FakeUDP(sockname="192.168.1.7"))
    assert wsd._local_ipv4() == "192.168.1.7"


def test_local_ipv4_falls_back_on_error(monkeypatch):
    monkeypatch.setattr(wsd.socket, "socket", lambda *a, **k: FakeUDP(connect_fails=True))
    assert wsd._local_ipv4() == "0.0.0.0"


# --- discover (full flow, faked socket) ---------------------------------------------

def test_discover_sends_probes_and_returns_unique_devices(monkeypatch):
    # one ProbeMatch reply, then the recv loop times out and breaks
    fake = FakeUDP(recv=[(_PROBE_MATCH, ("10.0.0.9", 3702))])
    monkeypatch.setattr(wsd.socket, "socket", lambda *a, **k: fake)
    monkeypatch.setattr(wsd, "_local_ipv4", lambda: "10.0.0.2")
    out = wsd.discover(timeout=0.2, retries=2)
    assert len(out) == 1 and out[0].address == "10.0.0.9"
    assert out[0].name == "Front Door"
    assert len(fake.sent) == 2          # one probe per retry
    assert fake.closed is True


def test_discover_empty_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(wsd.socket, "socket", lambda *a, **k: FakeUDP(recv=[]))
    monkeypatch.setattr(wsd, "_local_ipv4", lambda: "0.0.0.0")
    assert wsd.discover(timeout=0.1, retries=1) == []
