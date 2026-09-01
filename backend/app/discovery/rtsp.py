"""Minimal RTSP-over-TCP client primitives, standard library only.

RTSP is a text protocol very close to HTTP, so we speak it directly over a socket rather
than pulling a heavy dependency. This module is the low-level "how to talk RTSP" layer —
request/response parsing, Basic/Digest auth, and a reusable connection. Discovery
(``active_scan``) and, later, stream verification build on top of it.

Gentleness matters (see ``active_scan`` and docs/DECISIONS.md): the cameras run tiny
embedded RTSP servers, so :class:`RtspSession` reuses one connection and can throttle
between requests instead of opening a fresh socket per call.
"""
from __future__ import annotations

import base64
import hashlib
import re
import socket
import struct
import time
import uuid

USER_AGENT = "community-cam-guard/0.1"


def parse_status(response: bytes) -> int:
    """Return the numeric status from an RTSP response (0 if unparseable)."""
    # First line looks like: ``RTSP/1.0 200 OK``
    try:
        first = response.split(b"\r\n", 1)[0].decode("latin-1")
        return int(first.split(" ", 2)[1])
    except (IndexError, ValueError):
        return 0


def parse_headers(response: bytes) -> dict[str, str]:
    """Return the response headers as a lower-cased-key dict."""
    headers: dict[str, str] = {}
    head = response.split(b"\r\n\r\n", 1)[0]
    for line in head.split(b"\r\n")[1:]:
        if b":" in line:
            key, _, value = line.partition(b":")
            headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return headers


def parse_challenge(header_value: str) -> dict[str, str]:
    """Parse a ``WWW-Authenticate: Digest realm="..", nonce=".."`` value into params."""
    params: dict[str, str] = {}
    scheme, _, rest = header_value.partition(" ")
    params["scheme"] = scheme.lower()
    for part in rest.split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip().lower()] = value.strip().strip('"')
    return params


def digest_response(user: str, password: str, method: str, uri: str,
                    chal: dict[str, str]) -> str:
    """Build a Digest ``Authorization`` header value for the given challenge."""
    realm = chal.get("realm", "")
    nonce = chal.get("nonce", "")
    qop = chal.get("qop", "")

    def md5(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    ha1 = md5(f"{user}:{realm}:{password}")
    ha2 = md5(f"{method}:{uri}")

    fields = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
    if qop:
        cnonce = uuid.uuid4().hex[:16]
        nc = "00000001"
        resp = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        fields += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        resp = md5(f"{ha1}:{nonce}:{ha2}")
    fields.append(f'response="{resp}"')
    return "Digest " + ", ".join(fields)


def auth_header(resp: bytes, method: str, uri: str, username: str, password: str) -> str:
    """Answer a 401 with the right scheme (Digest if offered, else Basic)."""
    chal = parse_challenge(parse_headers(resp).get("www-authenticate", ""))
    if chal.get("scheme") == "digest":
        return digest_response(username, password, method, uri, chal)
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def parse_sdp(body: bytes | str) -> dict[str, object]:
    """Extract the media tracks a camera offers from a DESCRIBE SDP body.

    Returns ``{has_video, has_audio, video_codec, audio_codec}``. Codecs come from the
    ``a=rtpmap:<pt> <CODEC>/<clock>`` lines, associated with the preceding ``m=`` line.
    This is how we learn — without an ONVIF service — whether a camera has an audio
    (microphone) track worth exposing a "listen" control for.
    """
    text = body.decode("latin-1", "replace") if isinstance(body, bytes) else body
    # keep only the SDP section if a full response was passed in
    if "\r\n\r\n" in text:
        text = text.split("\r\n\r\n", 1)[1]
    result = {"has_video": False, "has_audio": False, "video_codec": "", "audio_codec": ""}
    current = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("m=video"):
            current = "video"
            result["has_video"] = True
        elif line.startswith("m=audio"):
            current = "audio"
            result["has_audio"] = True
        elif line.startswith("m="):
            current = ""
        elif line.startswith("a=rtpmap:") and current in ("video", "audio"):
            codec = line.split(" ", 1)[1].split("/", 1)[0].strip() if " " in line else ""
            key = f"{current}_codec"
            if codec and not result[key]:
                result[key] = codec
    return result


def check_credentials(ip: str, port: int, path: str, username: str = "", password: str = "",
                      timeout: float = 5.0) -> str:
    """Verify RTSP credentials by DESCRIBEing the camera's stream.

    Returns one of:
      - ``"ok"``          — DESCRIBE answered 200 (the credentials work)
      - ``"auth"``        — the camera challenged for auth and then **rejected** what we sent
      - ``"unreachable"`` — couldn't connect or the connection dropped with no usable answer
        (camera offline) — ambiguous, so the caller should not treat it as a credential failure.

    Once the camera issues its 401 challenge and we answer it, **only a 200 means the credentials
    are good**; any other status is a rejection. This firmware, for example, replies **400 Bad
    Request** (not another 401) to a wrong password — so we can't special-case 401. Only ``"auth"``
    is a definitive rejection; a genuinely offline camera stays addable.
    """
    uri = f"rtsp://{ip}:{port}{path}"
    try:
        session = RtspSession(ip, port, timeout)
    except OSError:
        return "unreachable"
    try:
        resp = session.request("DESCRIBE", uri, accept_sdp=True)
        if resp is None:
            return "unreachable"
        status = parse_status(resp)
        if status == 200:
            return "ok"                                # stream needs no auth
        if status != 401:
            return "unreachable"                       # unexpected first reply — not a challenge
        if not username:
            return "auth"                              # auth required but none supplied
        resp = session.request("DESCRIBE", uri, accept_sdp=True,
                               auth=auth_header(resp, "DESCRIBE", uri, username, password))
        if resp is None:
            return "unreachable"                       # dropped after auth — ambiguous
        return "ok" if parse_status(resp) == 200 else "auth"   # 200 works; anything else = rejected
    finally:
        session.close()


class RtspSession:
    """One persistent RTSP/TCP connection, reused for every request to a camera.

    Reusing a single connection (instead of a fresh socket per request) is the core of
    gentle probing: it turns many short-lived sockets into one. ``delay`` inserts a pause
    before each request after the first, to avoid machine-gunning the tiny embedded
    server. Responses are read fully (including any ``Content-Length`` body) so the stream
    stays aligned for the next request on the same connection.
    """

    def __init__(self, ip: str, port: int, timeout: float, delay: float = 0.0) -> None:
        self.ip = ip
        self.port = port
        self.base = f"rtsp://{ip}:{port}"
        self._delay = delay
        self._cseq = 0
        self._first = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect((ip, port))

    def request(self, method: str, uri: str, *, auth: str | None = None,
                accept_sdp: bool = False,
                extra_headers: dict[str, str] | None = None) -> bytes | None:
        """Send one RTSP request and return the full raw response (None on error).

        ``extra_headers`` appends arbitrary headers (used for control verbs like
        ``SET_PARAMETER``, which carry a ``Content-type: ptzCmd: ...`` line). A header
        whose value is empty is emitted valueless (``Session:``) to match firmware that
        expects exactly that form.
        """
        if not self._first and self._delay:
            time.sleep(self._delay)
        self._first = False
        self._cseq += 1
        lines = [f"{method} {uri} RTSP/1.0",
                 f"CSeq: {self._cseq}",
                 f"User-Agent: {USER_AGENT}"]
        if accept_sdp:
            lines.append("Accept: application/sdp")
        if auth:
            lines.append(f"Authorization: {auth}")
        for key, value in (extra_headers or {}).items():
            lines.append(f"{key}: {value}" if value != "" else f"{key}:")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        try:
            self._sock.sendall(request)
            return self._read_response()
        except OSError:
            return None

    def _read_response(self) -> bytes | None:
        buf = b""
        while b"\r\n\r\n" not in buf:
            data = self._sock.recv(4096)
            if not data:
                return buf or None
            buf += data
        head, _, body = buf.partition(b"\r\n\r\n")
        m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
        if m:
            need = int(m.group(1))
            while len(body) < need:
                data = self._sock.recv(4096)
                if not data:
                    break
                body += data
        return head + b"\r\n\r\n" + body

    def send_interleaved(self, channel: int, payload: bytes) -> None:
        """Send one RFC 2326 RTP-over-RTSP frame on the existing TCP connection.

        Some camera families use a proprietary RTSP control verb to enable a backchannel and
        then consume RTP on that same socket.  Keeping the framing primitive here lets those
        drivers own the codec and lifecycle without reaching into this session's private socket.
        """

        if not 0 <= channel <= 0xFF:
            raise ValueError("RTSP interleaved channel must fit in one byte")
        if not payload or len(payload) > 0xFFFF:
            raise ValueError("RTSP interleaved payload must contain 1..65535 bytes")
        self._sock.sendall(b"$" + bytes((channel,)) + struct.pack("!H", len(payload)) + payload)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> RtspSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
