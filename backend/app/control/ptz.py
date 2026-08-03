"""Pan/tilt control for Yoosee / generic-HiSilicon cameras — via ONVIF on port 5000.

These cameras do speak ONVIF, but **not** on the usual 80/8000: a full port scan finds it
on the proprietary-looking TCP **5000** (``/onvif/ptz_service``), answering SOAP **without
WS-Security**. (554 is RTSP video; 50000 is the app's P2P channel.) PTZ is driven with the
standard ONVIF ``ContinuousMove`` (a velocity in pan ``x`` / tilt ``y`` ∈ [-1, 1]) followed
by ``Stop``.

**This is the channel that actually moves the hardware.** An earlier attempt over RTSP
``SET_PARAMETER ptzCmd`` returned ``200`` but never moved our units (verified by frame diff);
ONVIF/5000 pans and tilts for real. Confirmed live: ``ContinuousMove`` on x/y visibly moves
the camera and a follow-up in the opposite direction returns it toward origin.

We expose discrete **pulses** (move in a direction for a short duration, then Stop) rather
than raw start/stop, so the camera always stops even if the caller disconnects — clicking a
D-pad arrow nudges the view; repeat to pan further.

Protocol details (from the community `victorbillyph/Yoosee-camera-documentation` and confirmed
on our cameras): endpoint ``http://<ip>:5000/onvif/ptz_service``, ``ProfileToken`` =
``IPCProfilesToken0`` (main stream). No auth required on our firmware.
"""
from __future__ import annotations

import logging
import socket
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.registry import Camera

log = logging.getLogger(__name__)

ONVIF_PTZ_PORT = 5000
PTZ_PATH = "/onvif/ptz_service"
PROFILE_TOKEN = "IPCProfilesToken0"
PULSE_SECONDS = 0.5  # how long one D-pad "step" moves before auto-Stop

# Friendly direction -> (pan x, tilt y) velocity. x: +right/-left, y: +up/-down.
DIRECTIONS: dict[str, tuple[float, float]] = {
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "up": (0.0, 1.0),
    "down": (0.0, -1.0),
}

_SOAP_HEAD = ('<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
              'xmlns:ptz="http://www.onvif.org/ver20/ptz/wsdl"><soap:Body>')
_SOAP_TAIL = "</soap:Body></soap:Envelope>"


def velocity_for(direction: str | None) -> tuple[float, float]:
    """Map a friendly direction to a (pan, tilt) velocity, or raise ValueError."""
    vel = DIRECTIONS.get((direction or "").strip().lower())
    if vel is None:
        raise ValueError(f"unknown PTZ direction {direction!r}; "
                         f"expected one of {sorted(DIRECTIONS)}")
    return vel


def _post_soap(ip: str, body: str, *, port: int = ONVIF_PTZ_PORT,
               timeout: float = 5.0) -> int | None:
    """POST a SOAP body to the camera's PTZ service; return HTTP status (None on error)."""
    url = f"http://{ip}:{port}{PTZ_PATH}"
    req = urllib.request.Request(
        url, data=(_SOAP_HEAD + body + _SOAP_TAIL).encode(),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


def _send_soap_nowait(ip: str, body: str, *, port: int = ONVIF_PTZ_PORT,
                      timeout: float = 2.0) -> bool:
    """Send a SOAP request and return **without waiting for the response**.

    The camera's ONVIF service takes ~0.7s to *respond* to a motion verb, but it acts on the
    command as soon as it arrives — so blocking on the response only adds latency and serialises
    press-and-hold repeats (making PTZ feel laggy vs. the vendor app's low-latency P2P path).
    We write the full request, half-close our side (a FIN tells the camera the request is
    complete so it processes it), and drop the socket. Returns True if the request was sent.
    """
    payload = (_SOAP_HEAD + body + _SOAP_TAIL).encode()
    http = (f"POST {PTZ_PATH} HTTP/1.1\r\nHost: {ip}:{port}\r\n"
            f"Content-Type: application/soap+xml; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode() + payload
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.sendall(http)
            try:
                sock.shutdown(socket.SHUT_WR)   # FIN: request complete → camera acts on it now
            except OSError:
                pass
        return True
    except OSError:
        return False


def _continuous_move(ip: str, x: float, y: float, *, port: int = ONVIF_PTZ_PORT,
                     wait: bool = True) -> int | None:
    body = (f"<ptz:ContinuousMove><ptz:ProfileToken>{PROFILE_TOKEN}</ptz:ProfileToken>"
            f'<ptz:Velocity><ptz:PanTilt x="{x}" y="{y}"/><ptz:Zoom x="0"/></ptz:Velocity>'
            "</ptz:ContinuousMove>")
    if wait:                                    # blocking (used by the capability probe)
        return _post_soap(ip, body, port=port)
    return 200 if _send_soap_nowait(ip, body, port=port) else None   # fire-and-forget for control


def stop(ip: str, *, port: int = ONVIF_PTZ_PORT) -> None:
    """Stop any pan/tilt motion — fire-and-forget (this firmware ignores Stop anyway; don't block)."""
    _send_soap_nowait(ip, (
        f"<ptz:Stop><ptz:ProfileToken>{PROFILE_TOKEN}</ptz:ProfileToken>"
        "<ptz:PanTilt>true</ptz:PanTilt><ptz:Zoom>false</ptz:Zoom></ptz:Stop>"
    ), port=port)


def move(camera: Camera, direction: str | None, *, duration: float = PULSE_SECONDS,
         port: int = ONVIF_PTZ_PORT) -> bool:
    """Pulse the camera one step in ``direction`` (ContinuousMove, then Stop after ``duration``).

    Raises ValueError for an unknown direction. Returns True if the move was accepted; always
    issues a Stop afterwards so the camera never keeps running on its own.
    """
    x, y = velocity_for(direction)
    if not camera.last_ip:
        return False
    status = _continuous_move(camera.last_ip, x, y, port=port, wait=False)
    try:
        if status != 200:
            return False
        time.sleep(max(0.0, duration))
        return True
    finally:
        stop(camera.last_ip, port=port)


def start(camera: Camera, direction: str | None, *, port: int = ONVIF_PTZ_PORT) -> bool:
    """Begin continuous motion in ``direction`` (no auto-Stop). Pair with :func:`halt`.

    This powers press-and-hold: the UI calls ``start`` on press and ``halt`` on release, so
    the camera pans smoothly for as long as the button is held instead of one nudge per click.
    Raises ValueError for an unknown direction; returns False if the camera has no known IP.
    """
    x, y = velocity_for(direction)
    if not camera.last_ip:
        return False
    return _continuous_move(camera.last_ip, x, y, port=port, wait=False) == 200


def halt(camera: Camera, *, port: int = ONVIF_PTZ_PORT) -> bool:
    """Stop motion started by :func:`start` (best-effort)."""
    if not camera.last_ip:
        return False
    stop(camera.last_ip, port=port)
    return True


def supports_ptz(ip: str, *, port: int = ONVIF_PTZ_PORT, timeout: float = 4.0) -> bool:
    """True if the camera drives ONVIF PTZ — a safe, non-moving probe.

    This minimal firmware only answers the motion verbs (the read-only ``GetConfigurations``
    / ``GetNodes`` queries just close the socket), so we probe with a **zero-velocity**
    ``ContinuousMove``: it returns 200 when PTZ is drivable but moves nothing (verified by
    frame diff). A Stop is issued afterwards for good measure.
    """
    status = _continuous_move(ip, 0.0, 0.0, port=port)
    if status == 200:
        stop(ip, port=port)
    return status == 200
