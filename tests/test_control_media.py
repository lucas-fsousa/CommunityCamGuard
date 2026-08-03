"""Tests for the ONVIF media service (backend/app/control/media.py). The SOAP poster `_post` is
stubbed with canned responses, so the parsing runs offline.
"""
from backend.app.control import media

_PROFILES = """<x><trt:GetProfilesResponse>
  <trt:Profiles token="IPCProfilesToken0"><trt:Name>Main</trt:Name></trt:Profiles>
  <trt:Profiles token="IPCProfilesToken1"><trt:Name>Sub</trt:Name></trt:Profiles>
  <trt:Profiles token="IPCProfilesToken0"><trt:Name>dup</trt:Name></trt:Profiles>
</trt:GetProfilesResponse></x>"""

_STREAM_URI = ("<x><trt:GetStreamUriResponse><trt:MediaUri>"
               "<tt:Uri>rtsp://10.0.0.9:554/onvif1</tt:Uri>"
               "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
               "</trt:MediaUri></trt:GetStreamUriResponse></x>")


def test_profile_tokens_parses_in_order_and_dedupes(monkeypatch):
    monkeypatch.setattr(media, "_post", lambda *a, **k: (200, _PROFILES))
    assert media.profile_tokens("10.0.0.9") == ["IPCProfilesToken0", "IPCProfilesToken1"]


def test_profile_tokens_empty_on_non_200(monkeypatch):
    monkeypatch.setattr(media, "_post", lambda *a, **k: (None, ""))
    assert media.profile_tokens("10.0.0.9") == []


def test_stream_uri_anchors_on_uri_not_mediauri(monkeypatch):
    monkeypatch.setattr(media, "_post", lambda *a, **k: (200, _STREAM_URI))
    assert media.stream_uri("10.0.0.9", "IPCProfilesToken0") == "rtsp://10.0.0.9:554/onvif1"


def test_stream_uri_none_when_absent(monkeypatch):
    monkeypatch.setattr(media, "_post", lambda *a, **k: (200, "<x>no uri here</x>"))
    assert media.stream_uri("10.0.0.9", "t") is None
    monkeypatch.setattr(media, "_post", lambda *a, **k: (500, "err"))
    assert media.stream_uri("10.0.0.9", "t") is None


def test_stream_paths_reduces_uris_to_paths(monkeypatch):
    monkeypatch.setattr(media, "profile_tokens", lambda ip, **k: ["T0", "T1"])
    uris = {"T0": "rtsp://10.0.0.9:554/onvif1", "T1": "rtsp://10.0.0.9:554/onvif2"}
    monkeypatch.setattr(media, "stream_uri", lambda ip, token, **k: uris[token])
    assert media.stream_paths("10.0.0.9") == ["/onvif1", "/onvif2"]


def test_stream_paths_skips_missing_uris_and_dedupes(monkeypatch):
    monkeypatch.setattr(media, "profile_tokens", lambda ip, **k: ["T0", "T1", "T2"])
    uris = {"T0": "rtsp://h:554/onvif1", "T1": None, "T2": "rtsp://h:554/onvif1"}
    monkeypatch.setattr(media, "stream_uri", lambda ip, token, **k: uris[token])
    assert media.stream_paths("10.0.0.9") == ["/onvif1"]   # None skipped, dup collapsed
