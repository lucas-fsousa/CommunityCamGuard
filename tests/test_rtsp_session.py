"""Tests for rtsp.RtspSession (backend/app/discovery/rtsp.py) — the tiny RTSP client. The socket
is faked, so request building and response reading are tested offline (the parse helpers have
their own tests in test_rtsp.py)."""
import pytest

from backend.app.discovery import rtsp


class FakeSock:
    def __init__(self, chunks=(), *, fail_send=False):
        self._chunks = list(chunks)
        self.sent = b""
        self.closed = False
        self._fail_send = fail_send

    def settimeout(self, t): pass
    def connect(self, addr): self.addr = addr
    def sendall(self, b):
        if self._fail_send:
            raise OSError("broken pipe")
        self.sent += b
    def recv(self, n):
        return self._chunks.pop(0) if self._chunks else b""
    def close(self):
        self.closed = True


def _session(monkeypatch, sock):
    monkeypatch.setattr(rtsp.socket, "socket", lambda *a, **k: sock)
    return rtsp.RtspSession("1.2.3.4", 554, 2.0)


def test_request_builds_the_rtsp_line_and_headers(monkeypatch):
    resp = b"RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: 5\r\n\r\nhello"
    sock = FakeSock([resp])
    s = _session(monkeypatch, sock)
    out = s.request("DESCRIBE", "rtsp://1.2.3.4/onvif1", accept_sdp=True,
                    auth="Digest abc", extra_headers={"Session": ""})
    assert out == resp                                   # full response (head + Content-Length body)
    sent = sock.sent.decode()
    assert sent.startswith("DESCRIBE rtsp://1.2.3.4/onvif1 RTSP/1.0\r\n")
    assert "CSeq: 1" in sent
    assert "Accept: application/sdp" in sent
    assert "Authorization: Digest abc" in sent
    assert "Session:\r\n" in sent                        # empty value -> emitted valueless


def test_cseq_increments_across_requests(monkeypatch):
    r = b"RTSP/1.0 200 OK\r\n\r\n"
    sock = FakeSock([r, r])
    s = _session(monkeypatch, sock)
    s.request("OPTIONS", "rtsp://1.2.3.4/")
    s.request("OPTIONS", "rtsp://1.2.3.4/")
    assert "CSeq: 1" in sock.sent.decode()
    assert "CSeq: 2" in sock.sent.decode()


def test_read_response_pulls_the_full_content_length_body(monkeypatch):
    # body arrives split across recv() calls; the reader must keep reading until Content-Length
    chunks = [b"RTSP/1.0 200 OK\r\nContent-Length: 10\r\n\r\nabcde", b"fghij"]
    sock = FakeSock(chunks)
    s = _session(monkeypatch, sock)
    out = s.request("DESCRIBE", "rtsp://1.2.3.4/x", accept_sdp=True)
    assert out.endswith(b"abcdefghij")


def test_request_returns_none_on_socket_error(monkeypatch):
    sock = FakeSock(fail_send=True)
    s = _session(monkeypatch, sock)
    assert s.request("OPTIONS", "rtsp://1.2.3.4/") is None


def test_close_is_safe_and_context_manager_works(monkeypatch):
    sock = FakeSock([b"RTSP/1.0 200 OK\r\n\r\n"])
    monkeypatch.setattr(rtsp.socket, "socket", lambda *a, **k: sock)
    with rtsp.RtspSession("1.2.3.4", 554, 2.0) as s:
        assert s.request("OPTIONS", "rtsp://1.2.3.4/") is not None
    assert sock.closed is True
