"""Read-only Wi-Fi discovery for factory provisioning.

Only fixed, argument-list subprocesses are used: no user value ever reaches a shell command.  The
SSID returned to the browser is paired with a short-lived signed identifier; the synchronization
endpoint accepts that identifier, not arbitrary SSID text.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from ..config import get_settings

TOKEN_MAX_AGE = 300
_TOKEN_SALT = "ccg-wifi-selection-v1"


class WifiSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int = 0
    security: str = ""

    def public(self) -> dict:
        return {
            "id": sign_network(self.ssid, self.security),
            "ssid": self.ssid,
            "signal": max(0, min(int(self.signal), 100)),
            "security": self.security,
        }


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().effective_signing_key, salt=_TOKEN_SALT)


def sign_network(ssid: str, security: str = "") -> str:
    return _serializer().dumps({"ssid": ssid, "security": security})


def manual_network(ssid: str, security: str) -> WifiNetwork:
    """Validate a localhost-only fallback selection before signing it like a scan result."""
    normalized_ssid = ssid.strip()
    if not normalized_ssid or len(normalized_ssid.encode("utf-8")) > 32:
        raise WifiSelectionError("SSID must contain 1 to 32 UTF-8 bytes")
    security_labels = {"wpa": "WPA/WPA2", "wep": "WEP", "open": "open"}
    try:
        normalized_security = security_labels[security.strip().lower()]
    except KeyError as exc:
        raise WifiSelectionError("unsupported Wi-Fi security") from exc
    return WifiNetwork(ssid=normalized_ssid, security=normalized_security)


def selected_network(token: str) -> WifiNetwork:
    try:
        payload = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except SignatureExpired as exc:
        raise WifiSelectionError("Wi-Fi selection expired; scan again") from exc
    except BadData as exc:
        raise WifiSelectionError("invalid Wi-Fi selection") from exc
    ssid = payload.get("ssid") if isinstance(payload, dict) else None
    security = payload.get("security", "") if isinstance(payload, dict) else ""
    if not isinstance(ssid, str) or not ssid or len(ssid.encode("utf-8")) > 32:
        raise WifiSelectionError("invalid Wi-Fi selection")
    if not isinstance(security, str) or len(security) > 128:
        raise WifiSelectionError("invalid Wi-Fi selection")
    return WifiNetwork(ssid=ssid, security=security)


def selected_ssid(token: str) -> str:
    """Compatibility helper for callers that only need the network name."""
    return selected_network(token).ssid


def _split_nmcli(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _parse_nmcli(output: str) -> list[WifiNetwork]:
    found: list[WifiNetwork] = []
    for line in output.splitlines():
        parts = _split_nmcli(line.strip())
        if len(parts) < 3:
            continue
        ssid, signal, security = parts[0], parts[1], ":".join(parts[2:])
        if not ssid or len(ssid.encode("utf-8")) > 32:
            continue
        try:
            strength = int(signal)
        except ValueError:
            strength = 0
        found.append(WifiNetwork(ssid, strength, security))
    return found


def _parse_iw(output: str) -> list[WifiNetwork]:
    found: list[WifiNetwork] = []
    ssid = ""
    signal = 0
    secure = False

    def flush() -> None:
        nonlocal ssid, signal, secure
        if ssid and len(ssid.encode("utf-8")) <= 32:
            found.append(WifiNetwork(ssid, signal, "secured" if secure else "open"))
        ssid, signal, secure = "", 0, False

    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("BSS "):
            flush()
        elif line.startswith("SSID: "):
            ssid = line[6:]
        elif line.startswith("signal: "):
            match = re.search(r"-?\d+(?:\.\d+)?", line[8:])
            if match:
                dbm = float(match.group(0))
                signal = round(max(0, min(100, 2 * (dbm + 100))))
        elif line.startswith(("RSN:", "WPA:")):
            secure = True
    flush()
    return found


def _dedupe(networks: list[WifiNetwork]) -> list[WifiNetwork]:
    strongest: dict[str, WifiNetwork] = {}
    for network in networks:
        previous = strongest.get(network.ssid)
        if previous is None or network.signal > previous.signal:
            strongest[network.ssid] = network
    return sorted(strongest.values(), key=lambda item: (-item.signal, item.ssid.casefold()))[:100]


def _run(command: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def scan_wifi_networks() -> tuple[list[WifiNetwork], str, str]:
    """Return ``(networks, scanner, error)`` without changing connection state."""
    if sys.platform == "win32" and shutil.which("netsh"):
        result = _run(["netsh", "wlan", "show", "networks", "mode=bssid"])
        if result.returncode == 0:
            networks = []
            for match in re.finditer(r"(?m)^SSID\s+\d+\s*:\s*(.+)$", result.stdout):
                ssid = match.group(1).strip()
                if ssid:
                    networks.append(WifiNetwork(ssid))
            return _dedupe(networks), "netsh", ""

    if shutil.which("nmcli"):
        result = _run(
            ["nmcli", "-t", "--escape", "yes", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"]
        )
        if result.returncode == 0:
            return _dedupe(_parse_nmcli(result.stdout)), "nmcli", ""

    if shutil.which("iw"):
        devices = _run(["iw", "dev"])
        interfaces = re.findall(r"(?m)^\s*Interface\s+(\S+)\s*$", devices.stdout)
        all_networks: list[WifiNetwork] = []
        for interface in interfaces:
            result = _run(["iw", "dev", interface, "scan"])
            if result.returncode == 0:
                all_networks.extend(_parse_iw(result.stdout))
        if all_networks:
            return _dedupe(all_networks), "iw", ""

    return [], "", "No Wi-Fi interface or supported scanner is available to the server"
