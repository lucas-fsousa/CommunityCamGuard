"""Transactional post-bind onboarding: LAN location, RTSP proof, then registry commit.

The generated clear credential exists only in process memory until ffprobe observes authenticated
media packets.  A P2P acknowledgement alone never creates or updates a camera registry row.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from ...camera_identity import normalize_identity, stable_camera_id
from ...db import p2p, registry
from ...db.p2p import P2PEnrollment
from ...discovery import active_scan
from ..base import Capabilities, classify_ports
from .p2p import (
    P2PProbeError,
    generate_rtsp_password,
    prepare_camera_rtsp,
    run_with_fresh_access,
    set_camera_rtsp_enabled,
)


class OnboardingCompletionError(RuntimeError):
    """Sanitized stage-aware failure suitable for the authenticated local API."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LocatedCamera:
    ip: str
    mac: str
    open_ports: tuple[int, ...]
    vendor: str
    model: str
    firmware: str


@dataclass(frozen=True, slots=True)
class RtspMediaProof:
    path: str
    transport: str
    has_video: bool
    has_audio: bool
    video_codec: str
    audio_codec: str
    packet_count: int


@dataclass(frozen=True, slots=True)
class CompletedCamera:
    camera: registry.Camera
    proof: RtspMediaProof
    stages: tuple[str, ...]
    already_configured: bool


def locate_camera_by_mac(mac: str) -> LocatedCamera:
    """Gently scan the local subnet and return only the exact printed camera MAC."""

    _kind, normalized = normalize_identity("mac", mac)
    canonical = ":".join(normalized[index : index + 2] for index in range(0, 12, 2))
    existing = registry.get_camera(canonical)
    if existing is not None and existing.last_ip:
        try:
            address = ipaddress.ip_address(existing.last_ip)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback):
            return LocatedCamera(
                existing.last_ip,
                canonical,
                tuple(existing.capabilities.get("open_ports") or (554, 5000)),
                existing.vendor,
                str(existing.capabilities.get("model") or ""),
                str(existing.capabilities.get("firmware") or ""),
            )

    for host in active_scan.scan(username="", password=""):
        matches = {value.lower() for value in (host.mac, host.arp_mac) if value}
        if canonical not in matches:
            continue
        try:
            address = ipaddress.ip_address(host.address)
        except ValueError:
            continue
        if not (address.is_private or address.is_loopback):
            continue
        return LocatedCamera(
            host.address,
            canonical,
            tuple(host.open_ports),
            host.vendor,
            host.model,
            host.firmware,
        )
    raise OnboardingCompletionError(
        "lan_discovery",
        "the exact camera MAC was not found on the local network",
    )


def _run_ffprobe(arguments: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )


def _parse_media_proof(raw: bytes, path: str, transport_name: str) -> RtspMediaProof | None:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        return None
    video_codec = ""
    audio_codec = ""
    packet_count = 0
    has_video = False
    has_audio = False
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        raw_packets = stream.get("nb_read_packets")
        if isinstance(raw_packets, int):
            packets = raw_packets
        elif isinstance(raw_packets, str) and raw_packets.isdigit():
            packets = int(raw_packets)
        else:
            packets = 0
        packet_count += packets
        kind = stream.get("codec_type")
        codec = stream.get("codec_name")
        if kind == "video" and packets > 0:
            has_video = True
            video_codec = str(codec or "")
        elif kind == "audio" and packets > 0:
            has_audio = True
            audio_codec = str(codec or "")
    if not has_video or packet_count <= 0:
        return None
    return RtspMediaProof(
        path, transport_name, has_video, has_audio, video_codec, audio_codec, packet_count
    )


def prove_rtsp_media(
    host: str,
    username: str,
    password: str,
    *,
    attempts: int = 3,
    timeout: float = 15.0,
    total_timeout: float = 60.0,
) -> RtspMediaProof:
    """Require authenticated packets, preferring the tested camera's UDP transport."""

    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise OnboardingCompletionError("media_proof", "camera LAN address is invalid") from exc
    if not (address.is_private or address.is_loopback):
        raise OnboardingCompletionError(
            "media_proof", "camera address is outside the local network"
        )
    if attempts < 1:
        raise ValueError("RTSP proof attempts must be positive")
    deadline = time.monotonic() + max(5.0, min(float(total_timeout), 90.0))
    for attempt in range(attempts):
        if attempt:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(2.0, remaining))
        for path in ("/onvif1", "/onvif2"):
            uri = f"rtsp://{username}:{password}@{host}:554{path}"
            for transport_name in ("udp", "tcp"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                arguments = (
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-rtsp_transport",
                    transport_name,
                    "-rw_timeout",
                    "5000000",
                    "-read_intervals",
                    "%+2",
                    "-count_packets",
                    "-show_entries",
                    "stream=codec_type,codec_name,nb_read_packets",
                    "-of",
                    "json",
                    uri,
                )
                try:
                    completed = _run_ffprobe(arguments, min(timeout, remaining))
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
                if completed.returncode != 0:
                    continue
                proof = _parse_media_proof(completed.stdout, path, transport_name)
                if proof is not None:
                    return proof
    raise OnboardingCompletionError(
        "media_proof",
        "RTSP credentials were delivered but no authenticated media packets were received",
    )


def _link_enrollment(enrollment: P2PEnrollment, public_id: str) -> P2PEnrollment:
    try:
        return p2p.link_enrollment_to_camera(enrollment.device_id, public_id)
    except ValueError as exc:
        raise OnboardingCompletionError(
            "identity", "camera identity could not be linked to its P2P enrollment"
        ) from exc


def _capabilities(
    located: LocatedCamera,
    proof: RtspMediaProof,
    firmware_hint: str,
) -> dict:
    caps = Capabilities(
        driver="yoosee",
        reachable=True,
        model=located.model,
        firmware=located.firmware or firmware_hint,
        has_video=proof.has_video,
        has_audio=proof.has_audio,
        video_codec=proof.video_codec,
        audio_codec=proof.audio_codec,
        stream_paths=[proof.path],
        open_ports=sorted(set(located.open_ports) | {554}),
        probed_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    caps.ports_by_role = classify_ports(caps.open_ports)
    return caps.to_dict()


def complete_camera_onboarding(
    enrollment: P2PEnrollment,
    located: LocatedCamera,
    *,
    device_id: str,
    name: str = "",
    firmware_hint: str = "",
) -> CompletedCamera:
    """Commit a new camera only after exact P2P targeting and authenticated media proof."""

    if enrollment.device_id != str(device_id):
        raise OnboardingCompletionError("identity", "P2P enrollment belongs to another camera")
    public_id = stable_camera_id("mac", located.mac)
    if enrollment.camera_id not in (None, public_id):
        raise OnboardingCompletionError(
            "identity", "P2P enrollment is linked to a different camera identity"
        )

    existing = registry.get_camera(located.mac)
    if existing is not None:
        if not existing.password or not existing.last_ip or not existing.stream_path:
            raise OnboardingCompletionError(
                "registry",
                "this MAC is already registered without a verifiable RTSP credential",
            )
        proof = prove_rtsp_media(
            existing.last_ip, existing.username or "admin", existing.password, attempts=1
        )
        if enrollment.camera_id is None:
            _link_enrollment(enrollment, public_id)
        return CompletedCamera(
            existing,
            proof,
            ("identity", "lan_discovery", "existing_media_proof", "registry"),
            True,
        )

    if enrollment.camera_id is None:
        enrollment = _link_enrollment(enrollment, public_id)

    password = generate_rtsp_password()
    try:
        prepared = run_with_fresh_access(
            enrollment, lambda current: prepare_camera_rtsp(current, password)
        )
    except P2PProbeError as exc:
        raise OnboardingCompletionError("rtsp_configuration", str(exc)) from exc

    try:
        proof = prove_rtsp_media(located.ip, "admin", password)
    except OnboardingCompletionError:
        if not prepared.previous_enabled:
            current = p2p.get_enrollment(enrollment.device_id)
            if current is not None:
                try:
                    run_with_fresh_access(
                        current, lambda selected: set_camera_rtsp_enabled(selected, False)
                    )
                except P2PProbeError:
                    pass
        raise

    camera = registry.upsert_camera(
        located.mac,
        name=name.strip() or f"Camera {located.mac[-5:].replace(':', '').upper()}",
        username="admin",
        password=password,
        stream_path=proof.path,
        rtsp_port=554,
        last_ip=located.ip,
        vendor=located.vendor or "Yoosee / Gwell",
        capabilities=_capabilities(located, proof, firmware_hint),
        identity_kind="mac",
        identity_value=located.mac,
    )
    return CompletedCamera(
        camera,
        proof,
        (
            "identity",
            "lan_discovery",
            "p2p_session",
            "rtsp_enabled",
            "credential_delivered",
            "media_proof",
            "registry",
        ),
        False,
    )
