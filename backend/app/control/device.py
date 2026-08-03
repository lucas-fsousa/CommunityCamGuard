"""Device-level ONVIF control — software reboot + device info, over port 5000.

Same ONVIF service the cameras expose for PTZ (see :mod:`.ptz`), but the **device service**
(`/onvif/device_service`, namespace `tds`) rather than the PTZ one. It answers
`GetDeviceInformation` (model/firmware), **`GetNetworkInterfaces`** (the camera's own MAC —
authoritative, ARP-independent identity) and, importantly, **`SystemReboot`** — a clean
restart with no power cycle. No WS-Security on our units.

Kept separate from PTZ because it actuates the whole device, not the gimbal; both talk the
same host but different service paths, so the tiny SOAP poster is duplicated on purpose to
keep each control path independent.
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.registry import Camera

log = logging.getLogger(__name__)

ONVIF_PORT = 5000
DEVICE_PATH = "/onvif/device_service"
TDS = "http://www.onvif.org/ver10/device/wsdl"   # ONVIF device wsdl namespace


def _post(ip: str, body: str, *, port: int = ONVIF_PORT, timeout: float = 6.0) -> tuple[int | None, str]:
    """POST a SOAP body to the ONVIF device service; return (status, text)."""
    env = ('<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
           f'<soap:Body>{body}</soap:Body></soap:Envelope>')
    req = urllib.request.Request(f"http://{ip}:{port}{DEVICE_PATH}", data=env.encode(),
                                 headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("latin-1", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("latin-1", "replace")
    except OSError:
        return None, ""


def info(ip: str, *, port: int = ONVIF_PORT, timeout: float = 4.0) -> dict | None:
    """Return `{manufacturer, model, firmware, serial, hardware}` from the device, or None."""
    status, body = _post(ip, f'<tds:GetDeviceInformation xmlns:tds="{TDS}"/>', port=port, timeout=timeout)
    if status != 200:
        return None

    def field(tag: str) -> str:
        m = re.search(rf"<[^>]*{tag}>([^<]*)</", body)
        return m.group(1).strip() if m else ""

    return {
        "manufacturer": field("Manufacturer"), "model": field("Model"),
        "firmware": field("FirmwareVersion"), "serial": field("SerialNumber"),
        "hardware": field("HardwareId"),
    }


def _normalize_mac(raw: str) -> str | None:
    """Normalise an ONVIF ``HwAddress`` (``F4-E2-5D-..`` / ``F4:E2:5D:..`` / bare hex) to
    lower-case ``aa:bb:cc:dd:ee:ff``. Returns None if it isn't 12 hex digits or is all-zero."""
    hexs = re.sub(r"[^0-9a-fA-F]", "", raw or "")
    if len(hexs) != 12:
        return None
    mac = ":".join(hexs[i:i + 2] for i in range(0, 12, 2)).lower()
    return None if mac == "00:00:00:00:00:00" else mac


def mac_address(ip: str, *, port: int = ONVIF_PORT, timeout: float = 4.0) -> str | None:
    """Read the camera's own MAC via ONVIF ``GetNetworkInterfaces`` (the ``HwAddress`` field).

    This is the **authoritative, ARP-independent** identity: unlike ``/proc/net/arp`` (which only
    works on the same L2 segment, i.e. WSL ``mirrored``), the camera reports its own hardware
    address, so identity survives a non-mirrored/containerised deployment. No-auth on our units.
    Returns a normalised ``aa:bb:cc:dd:ee:ff`` (first interface with a valid address), or None.
    """
    status, body = _post(ip, f'<tds:GetNetworkInterfaces xmlns:tds="{TDS}"/>', port=port, timeout=timeout)
    if status != 200:
        return None
    for m in re.finditer(r"<[^>]*HwAddress>([^<]*)</", body):
        mac = _normalize_mac(m.group(1))
        if mac:
            return mac
    return None


def supports_reboot(ip: str, *, port: int = ONVIF_PORT, timeout: float = 4.0) -> bool:
    """True if the ONVIF device service answers — a read-only probe (SystemReboot is then available)."""
    return info(ip, port=port, timeout=timeout) is not None


def reboot(camera: Camera, *, port: int = ONVIF_PORT) -> bool:
    """Reboot the camera via ONVIF ``SystemReboot`` (no power cycle). True if accepted.

    The camera goes offline for ~30-60s and comes back on the same MAC (its IP is refreshed by
    the next scan). Returns False if the camera has no known IP or rejects the request.
    """
    if not camera.last_ip:
        return False
    status, _ = _post(camera.last_ip, f'<tds:SystemReboot xmlns:tds="{TDS}"/>', port=port)
    return status == 200
