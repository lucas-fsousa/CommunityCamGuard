from backend.app.discovery import rtsp


def test_parse_status():
    assert rtsp.parse_status(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n") == 200
    assert rtsp.parse_status(b"RTSP/1.0 401 Unauthorized\r\n\r\n") == 401
    assert rtsp.parse_status(b"garbage") == 0


def test_parse_headers_lowercased():
    h = rtsp.parse_headers(b"RTSP/1.0 401 X\r\nWWW-Authenticate: Digest realm=\"cam\"\r\n\r\n")
    assert h["www-authenticate"].startswith("Digest")


def test_parse_challenge():
    c = rtsp.parse_challenge('Digest realm="cam", nonce="abc", qop="auth"')
    assert c["scheme"] == "digest"
    assert c["realm"] == "cam"
    assert c["nonce"] == "abc"


def test_digest_response_is_deterministic_without_qop():
    chal = {"realm": "cam", "nonce": "abc"}
    a = rtsp.digest_response("admin", "pw", "DESCRIBE", "rtsp://h/s", chal)
    b = rtsp.digest_response("admin", "pw", "DESCRIBE", "rtsp://h/s", chal)
    assert a == b
    assert 'username="admin"' in a and "response=" in a


def test_auth_header_picks_basic_when_no_digest():
    resp = b"RTSP/1.0 401 X\r\nWWW-Authenticate: Basic realm=\"cam\"\r\n\r\n"
    assert rtsp.auth_header(resp, "DESCRIBE", "rtsp://h/s", "admin", "pw").startswith("Basic ")


def test_auth_header_picks_digest_when_offered():
    resp = b"RTSP/1.0 401 X\r\nWWW-Authenticate: Digest realm=\"cam\", nonce=\"n\"\r\n\r\n"
    assert rtsp.auth_header(resp, "DESCRIBE", "rtsp://h/s", "admin", "pw").startswith("Digest ")


# --- credential verification (check_credentials) -----------------------------------

class _FakeSession:
    """Stand-in for RtspSession that replays canned DESCRIBE responses."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
    def request(self, method, uri, **kw):
        self.sent.append((method, kw.get("auth")))
        return self.responses.pop(0) if self.responses else None
    def close(self):
        pass


def _patch_session(monkeypatch, responses):
    monkeypatch.setattr(rtsp, "RtspSession", lambda ip, port, timeout: _FakeSession(responses))


def test_check_credentials_ok_on_200(monkeypatch):
    _patch_session(monkeypatch, [b"RTSP/1.0 200 OK\r\n\r\n"])
    assert rtsp.check_credentials("1.2.3.4", 554, "/onvif1", "admin", "pw") == "ok"


def test_check_credentials_auth_when_401_persists(monkeypatch):
    chal = b'RTSP/1.0 401 Unauthorized\r\nWWW-Authenticate: Digest realm="cam", nonce="n"\r\n\r\n'
    _patch_session(monkeypatch, [chal, chal])            # challenge, then still 401 after auth
    assert rtsp.check_credentials("1.2.3.4", 554, "/onvif1", "admin", "wrong") == "auth"


def test_check_credentials_auth_on_400_after_challenge(monkeypatch):
    # Real firmware behaviour: wrong password -> 400 Bad Request (not another 401) after auth.
    chal = b'RTSP/1.0 401 Unauthorized\r\nWWW-Authenticate: Digest realm="cam", nonce="n"\r\n\r\n'
    _patch_session(monkeypatch, [chal, b"RTSP/1.0 400 Bad Request\r\n\r\n"])
    assert rtsp.check_credentials("1.2.3.4", 554, "/onvif1", "admin", "wrong") == "auth"


def test_check_credentials_auth_when_challenge_but_no_username(monkeypatch):
    chal = b'RTSP/1.0 401 Unauthorized\r\nWWW-Authenticate: Digest realm="cam", nonce="n"\r\n\r\n'
    _patch_session(monkeypatch, [chal])
    assert rtsp.check_credentials("1.2.3.4", 554, "/onvif1", "", "") == "auth"


def test_check_credentials_ok_after_answering_challenge(monkeypatch):
    chal = b'RTSP/1.0 401 Unauthorized\r\nWWW-Authenticate: Digest realm="cam", nonce="n"\r\n\r\n'
    _patch_session(monkeypatch, [chal, b"RTSP/1.0 200 OK\r\n\r\n"])
    assert rtsp.check_credentials("1.2.3.4", 554, "/onvif1", "admin", "pw") == "ok"


def test_check_credentials_unreachable_when_connect_fails(monkeypatch):
    def boom(ip, port, timeout):
        raise OSError("refused")
    monkeypatch.setattr(rtsp, "RtspSession", boom)
    assert rtsp.check_credentials("1.2.3.4", 554, "/onvif1", "admin", "pw") == "unreachable"


def test_check_credentials_unreachable_on_no_response(monkeypatch):
    _patch_session(monkeypatch, [None])
    assert rtsp.check_credentials("1.2.3.4", 554, "/onvif1", "admin", "pw") == "unreachable"
