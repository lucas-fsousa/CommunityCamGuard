"""ONVIF **Media** service — profile + stream-URI discovery, over port 5000.

The same ONVIF stack that carries PTZ (:mod:`.ptz`) and device info (:mod:`.device`) also
exposes a media service at ``/onvif/media_service``. Unlike the audio-output operations
(unimplemented on these units — see docs/DECISIONS.md), ``GetProfiles`` and ``GetStreamUri``
*do* answer, so a driver can ask the camera for its **real** RTSP paths instead of guessing
from a hard-coded list.

Read-only. No WS-Security on our units (same as the other services here).
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request

ONVIF_PORT = 5000
MEDIA_PATH = "/onvif/media_service"
TRT = "http://www.onvif.org/ver10/media/wsdl"    # ONVIF media wsdl namespace
SCH = "http://www.onvif.org/ver10/schema"        # ONVIF schema (StreamSetup types)


def _post(ip: str, body: str, *, port: int = ONVIF_PORT, timeout: float = 6.0) -> tuple[int | None, str]:
    """POST a SOAP body to the ONVIF media service; return (status, text)."""
    env = ('<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
           f'<soap:Body>{body}</soap:Body></soap:Envelope>')
    req = urllib.request.Request(f"http://{ip}:{port}{MEDIA_PATH}", data=env.encode(),
                                 headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("latin-1", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("latin-1", "replace")
    except OSError:
        return None, ""


def profile_tokens(ip: str, *, port: int = ONVIF_PORT, timeout: float = 6.0) -> list[str]:
    """Return the media profile tokens (main first), or ``[]`` if the service doesn't answer."""
    status, body = _post(ip, f'<trt:GetProfiles xmlns:trt="{TRT}"/>', port=port, timeout=timeout)
    if status != 200:
        return []
    # Each profile is <...Profiles token="IPCProfilesToken0" ...>; keep source order, dedupe.
    seen: list[str] = []
    for tok in re.findall(r'<(?:\w+:)?Profiles\b[^>]*token="([^"]+)"', body):
        if tok not in seen:
            seen.append(tok)
    return seen


def stream_uri(ip: str, token: str, *, port: int = ONVIF_PORT, timeout: float = 6.0) -> str | None:
    """Return the RTSP URI for a profile token (e.g. ``rtsp://ip:554/onvif1``), or ``None``.

    Note the ``<tt:Uri>`` is anchored so it is not confused with the ``<tt:MediaUri>`` wrapper.
    """
    body = (f'<trt:GetStreamUri xmlns:trt="{TRT}"><trt:StreamSetup>'
            f'<tt:Stream xmlns:tt="{SCH}">RTP-Unicast</tt:Stream>'
            f'<tt:Transport xmlns:tt="{SCH}"><tt:Protocol>RTSP</tt:Protocol></tt:Transport>'
            f'</trt:StreamSetup><trt:ProfileToken>{token}</trt:ProfileToken></trt:GetStreamUri>')
    status, text = _post(ip, body, port=port, timeout=timeout)
    if status != 200:
        return None
    m = re.search(r"<(?:\w+:)?Uri>([^<]+)</(?:\w+:)?Uri>", text)
    return m.group(1).strip() if m else None


def stream_paths(ip: str, *, port: int = ONVIF_PORT, timeout: float = 6.0) -> list[str]:
    """ONVIF-discovered RTSP **paths** (``/onvif1``, ...), main first, deduped.

    Asks the media service for every profile's stream URI and reduces each to its path. Returns
    ``[]`` when the service is absent or partial (the caller then keeps its hard-coded guesses).
    """
    paths: list[str] = []
    for token in profile_tokens(ip, port=port, timeout=timeout):
        uri = stream_uri(ip, token, port=port, timeout=timeout)
        if not uri:
            continue
        path = re.sub(r"^rtsp://[^/]+", "", uri)   # strip scheme+host:port, keep the path
        if path and path not in paths:
            paths.append(path)
    return paths
