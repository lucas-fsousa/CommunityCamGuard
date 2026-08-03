"""Tests for the device-control + capability layer (no camera hardware required)."""
from __future__ import annotations

import pytest

from backend.app import drivers
from backend.app.control import device, media, ptz
from backend.app.db.registry import Camera
from backend.app.discovery import active_scan, rtsp

# --- SDP parsing (rtsp.parse_sdp) --------------------------------------------------

SDP = ("v=0\r\n"
       "m=video 0 RTP/AVP 96\r\n"
       "a=rtpmap:96 H265/90000\r\n"
       "m=audio 0 RTP/AVP 8\r\n"
       "a=rtpmap:8 PCMA/8000\r\n")


def test_parse_sdp_detects_video_and_audio():
    r = rtsp.parse_sdp(SDP)
    assert r == {"has_video": True, "has_audio": True,
                 "video_codec": "H265", "audio_codec": "PCMA"}


def test_parse_sdp_video_only():
    r = rtsp.parse_sdp("m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n")
    assert r["has_video"] and not r["has_audio"]
    assert r["video_codec"] == "H264" and r["audio_codec"] == ""


def test_parse_sdp_strips_response_head_and_accepts_bytes():
    resp = b"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\n\r\n" + SDP.encode()
    assert rtsp.parse_sdp(resp)["audio_codec"] == "PCMA"


# --- PTZ velocity mapping (ONVIF ContinuousMove) -----------------------------------

def test_velocity_maps_directions_to_pan_tilt():
    assert ptz.velocity_for("left") == (-1.0, 0.0)
    assert ptz.velocity_for("Right") == (1.0, 0.0)
    assert ptz.velocity_for(" up ") == (0.0, 1.0)
    assert ptz.velocity_for("DOWN") == (0.0, -1.0)


def test_velocity_rejects_unknown():
    with pytest.raises(ValueError):
        ptz.velocity_for("zoom")


def test_velocity_rejects_none():
    with pytest.raises(ValueError):
        ptz.velocity_for(None)


def test_start_and_halt_without_ip_return_false():
    cam = Camera(mac="aa:bb", last_ip="")
    assert ptz.start(cam, "left") is False
    assert ptz.halt(cam) is False


def test_start_rejects_bad_direction_before_network():
    cam = Camera(mac="aa:bb", last_ip="1.2.3.4")
    with pytest.raises(ValueError):
        ptz.start(cam, "zoom")


def test_reboot_without_ip_returns_false():
    assert device.reboot(Camera(mac="aa:bb", last_ip="")) is False


# --- ONVIF MAC (GetNetworkInterfaces) — authoritative, ARP-independent identity ----

def test_normalize_mac_accepts_dash_colon_and_bare_hex():
    assert device._normalize_mac("AA-BB-CC-DD-EE-01") == "aa:bb:cc:dd:ee:01"
    assert device._normalize_mac("AA:BB:CC:DD:EE:01") == "aa:bb:cc:dd:ee:01"
    assert device._normalize_mac("aabbccddee01") == "aa:bb:cc:dd:ee:01"


def test_normalize_mac_rejects_bad_or_zero():
    assert device._normalize_mac("00:00:00:00:00:00") is None
    assert device._normalize_mac("nope") is None
    assert device._normalize_mac("aa:bb:cc") is None          # too short


def test_mac_address_parses_hwaddress(monkeypatch):
    body = ('<tds:GetNetworkInterfacesResponse><tds:NetworkInterfaces token="eth0">'
            '<tt:Info><tt:Name>eth0</tt:Name><tt:HwAddress>AA-BB-CC-DD-EE-01</tt:HwAddress>'
            '</tt:Info></tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>')
    monkeypatch.setattr(device, "_post", lambda ip, b, **k: (200, body))
    assert device.mac_address("1.2.3.4") == "aa:bb:cc:dd:ee:01"


def test_mac_address_none_when_service_absent(monkeypatch):
    monkeypatch.setattr(device, "_post", lambda ip, b, **k: (None, ""))
    assert device.mac_address("1.2.3.4") is None


def test_mac_address_skips_zero_interface_takes_real_one(monkeypatch):
    body = ("<x><tt:HwAddress>00:00:00:00:00:00</tt:HwAddress>"
            "<tt:HwAddress>AA:BB:CC:DD:EE:02</tt:HwAddress></x>")
    monkeypatch.setattr(device, "_post", lambda ip, b, **k: (200, body))
    assert device.mac_address("1.2.3.4") == "aa:bb:cc:dd:ee:02"


def test_move_returns_false_without_ip():
    cam = Camera(mac="aa:bb", last_ip="")
    assert ptz.move(cam, "up") is False


def test_move_rejects_bad_direction_before_touching_network():
    cam = Camera(mac="aa:bb", last_ip="1.2.3.4")
    with pytest.raises(ValueError):
        ptz.move(cam, "diagonal")


def test_move_pulses_then_stops(monkeypatch):
    """A successful move issues ContinuousMove and always follows with Stop."""
    calls = []
    monkeypatch.setattr(ptz, "_continuous_move", lambda ip, x, y, **k: calls.append(("move", ip, x, y)) or 200)
    monkeypatch.setattr(ptz, "stop", lambda ip, **k: calls.append(("stop", ip)))
    monkeypatch.setattr(ptz.time, "sleep", lambda s: None)
    cam = Camera(mac="aa:bb", last_ip="1.2.3.4")
    assert ptz.move(cam, "left") is True
    assert calls == [("move", "1.2.3.4", -1.0, 0.0), ("stop", "1.2.3.4")]


def test_move_stops_even_when_camera_rejects(monkeypatch):
    calls = []
    monkeypatch.setattr(ptz, "_continuous_move", lambda ip, x, y, **k: 500)
    monkeypatch.setattr(ptz, "stop", lambda ip, **k: calls.append("stop"))
    cam = Camera(mac="aa:bb", last_ip="1.2.3.4")
    assert ptz.move(cam, "left") is False
    assert calls == ["stop"]           # Stop is issued even on a failed move


# --- ONVIF media service (profile + stream-URI discovery) --------------------------

_PROFILES = (
    '<trt:GetProfilesResponse xmlns:trt="x">'
    '<trt:Profiles token="IPCProfilesToken0" fixed="true"><trt:Name>MainStream</trt:Name></trt:Profiles>'
    '<trt:Profiles token="IPCProfilesToken1"><trt:Name>SubStream</trt:Name></trt:Profiles>'
    '</trt:GetProfilesResponse>')

# The real Uri is wrapped in <tt:MediaUri>; stream_uri must not grab the wrapper tag.
_STREAM = ('<trt:GetStreamUriResponse><trt:MediaUri>'
           '<tt:Uri>rtsp://192.168.1.101:554/onvif%s</tt:Uri>'
           '<tt:InvalidAfterConnect>true</tt:InvalidAfterConnect></trt:MediaUri></trt:GetStreamUriResponse>')


def test_profile_tokens_parsed_main_first(monkeypatch):
    monkeypatch.setattr(media, "_post", lambda ip, body, **k: (200, _PROFILES))
    assert media.profile_tokens("1.2.3.4") == ["IPCProfilesToken0", "IPCProfilesToken1"]


def test_profile_tokens_empty_when_service_absent(monkeypatch):
    monkeypatch.setattr(media, "_post", lambda ip, body, **k: (None, ""))
    assert media.profile_tokens("1.2.3.4") == []


def test_stream_uri_anchors_past_mediauri_wrapper(monkeypatch):
    monkeypatch.setattr(media, "_post", lambda ip, body, **k: (200, _STREAM % "1"))
    assert media.stream_uri("1.2.3.4", "IPCProfilesToken0") == "rtsp://192.168.1.101:554/onvif1"


def test_stream_paths_reduces_uris_to_deduped_paths(monkeypatch):
    def fake(ip, body, **k):
        if "GetProfiles" in body:
            return 200, _PROFILES
        # main -> /onvif1, sub -> /onvif2
        n = "2" if "Token1" in body else "1"
        return 200, _STREAM % n
    monkeypatch.setattr(media, "_post", fake)
    assert media.stream_paths("1.2.3.4") == ["/onvif1", "/onvif2"]


def test_stream_paths_empty_on_partial_stack(monkeypatch):
    # GetProfiles answers but GetStreamUri closes the connection (partial ONVIF stack).
    def fake(ip, body, **k):
        return (200, _PROFILES) if "GetProfiles" in body else (None, "")
    monkeypatch.setattr(media, "_post", fake)
    assert media.stream_paths("1.2.3.4") == []


# --- no-auth identification during scan (port-agnostic, before any login) ----------

def test_identify_probes_open_ports_not_a_fixed_onvif_port(monkeypatch):
    """Identification must NOT assume ONVIF is on :5000 — it tries the open ports."""
    tried = []
    def info(ip, *, port, timeout):
        tried.append(port)
        return {"manufacturer": "Acme", "model": "CamX", "firmware": "1.2"} if port == 8000 else None
    monkeypatch.setattr(device, "info", info)
    monkeypatch.setattr(device, "mac_address", lambda ip, **k: None)
    monkeypatch.setattr(media, "stream_paths", lambda ip, **k: ["/live"])
    host = active_scan.ScannedHost(address="1.2.3.4", open_ports=[554, 80, 8000])  # ONVIF on 8000
    active_scan._identify(host)
    assert host.model == "CamX" and host.stream_paths == ["/live"]
    assert 554 not in tried                 # RTSP port skipped (can't speak SOAP)
    assert tried == [80, 8000]              # tried open non-RTSP ports in order, matched on 8000


def test_identify_fills_identity_and_detects_yoosee(monkeypatch):
    monkeypatch.setattr(device, "info",
                        lambda ip, *, port, timeout: {"manufacturer": "Technology", "model": "IPC",
                                                      "firmware": "40.01.22"} if port == 5000 else None)
    monkeypatch.setattr(device, "mac_address", lambda ip, **k: None)
    monkeypatch.setattr(media, "stream_paths", lambda ip, **k: ["/onvif1", "/onvif2"])
    host = active_scan.ScannedHost(address="1.2.3.4", open_ports=[554, 5000, 50000])
    active_scan._identify(host)
    assert host.model == "IPC" and host.firmware == "40.01.22" and host.vendor == "Technology"
    assert host.stream_paths == ["/onvif1", "/onvif2"] and host.driver == "yoosee"


def test_identify_prefers_onvif_mac_over_arp(monkeypatch):
    """The camera's own MAC (ONVIF GetNetworkInterfaces) overrides the ARP-derived one."""
    monkeypatch.setattr(device, "info",
                        lambda ip, *, port, timeout: {"manufacturer": "T", "model": "IPC", "firmware": "1"}
                        if port == 5000 else None)
    monkeypatch.setattr(media, "stream_paths", lambda ip, **k: ["/onvif1"])
    monkeypatch.setattr(device, "mac_address", lambda ip, *, port, timeout: "aa:bb:cc:dd:ee:01"
                        if port == 5000 else None)
    host = active_scan.ScannedHost(address="1.2.3.4", mac="aa:bb:cc:dd:ee:ff",  # stale ARP value
                                   open_ports=[554, 5000])
    active_scan._identify(host)
    assert host.mac == "aa:bb:cc:dd:ee:01"        # authoritative ONVIF MAC wins


def test_identify_keeps_arp_mac_when_onvif_has_none(monkeypatch):
    monkeypatch.setattr(device, "info",
                        lambda ip, *, port, timeout: {"manufacturer": "T", "model": "IPC", "firmware": "1"})
    monkeypatch.setattr(media, "stream_paths", lambda ip, **k: [])
    monkeypatch.setattr(device, "mac_address", lambda ip, **k: None)   # ONVIF didn't report it
    host = active_scan.ScannedHost(address="1.2.3.4", mac="aa:bb:cc:dd:ee:ff", open_ports=[5000])
    active_scan._identify(host)
    assert host.mac == "aa:bb:cc:dd:ee:ff"        # ARP fallback preserved


def test_identify_skips_soap_probe_when_only_rtsp_open(monkeypatch):
    called = []
    monkeypatch.setattr(device, "info", lambda ip, **k: called.append(k.get("port")) or None)
    host = active_scan.ScannedHost(address="1.2.3.4", open_ports=[554])   # nothing to SOAP-probe
    active_scan._identify(host)
    assert called == [] and host.model == "" and host.driver == "generic"


def test_identify_degrades_gracefully_when_nothing_answers(monkeypatch):
    tried = []
    monkeypatch.setattr(device, "info", lambda ip, *, port, timeout: tried.append(port) or None)
    monkeypatch.setattr(media, "stream_paths", lambda ip, **k: [])
    host = active_scan.ScannedHost(address="1.2.3.4", open_ports=[554, 5000, 50000])
    active_scan._identify(host)
    assert tried == [5000, 50000]             # likely port (5000) first, then the rest; RTSP skipped
    assert host.model == "" and host.driver == "yoosee"   # still driver-detected from ports


# --- port classification -----------------------------------------------------------

def test_classify_ports_groups_by_role():
    grouped = drivers.classify_ports([554, 5000, 50000, 80, 12345])
    assert grouped["rtsp"] == [554]
    assert grouped["onvif-ptz"] == [5000]      # ONVIF service, not "proprietary"
    assert grouped["p2p"] == [50000]
    assert grouped["http"] == [80]
    assert grouped["unknown"] == [12345]


def test_wide_ports_include_proprietary_control():
    assert 5000 in active_scan.WIDE_PORTS
    assert 50000 in active_scan.WIDE_PORTS
    # WIDE_PORTS is a superset of the subnet-sweep list
    assert set(active_scan.DEFAULT_PORTS) <= set(active_scan.WIDE_PORTS)


def test_capabilities_to_dict_roundtrips_fields():
    caps = drivers.Capabilities(driver="yoosee", reachable=True, ptz=True, ptz_protocol="onvif",
                                has_audio=True, audio_codec="PCMA", open_ports=[554])
    d = caps.to_dict()
    assert d["driver"] == "yoosee" and d["ptz"] and d["audio_codec"] == "PCMA"
