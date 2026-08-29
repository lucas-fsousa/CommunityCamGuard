"""Private read-only orchestration for sanitized Yoosee alarm-voice catalogues."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

from ....db.p2p import P2PEnrollment
from .alarm_voice import (
    AlarmVoiceCatalog,
    AlarmVoiceResource,
    build_alarm_voice_query,
    decode_alarm_voice_catalog,
)
from .camera_session import open_camera_session
from .contracts import P2PProbeError
from .resource_service_session import exchange_alarm_voice_catalog


@dataclass(frozen=True, slots=True)
class P2PAlarmVoiceCatalog:
    """Sanitized catalogue result; opaque vendor ids remain private and non-repr."""

    device_id: str
    system_total: int
    custom_total: int
    transport_acknowledged: bool
    resources: tuple[AlarmVoiceResource, ...] = field(repr=False)

    def public_options(self) -> tuple[dict[str, object], ...]:
        return tuple(resource.public() for resource in self.resources)


def _decode_response(
    payload: bytes | None,
    status_code: int | None,
) -> AlarmVoiceCatalog:
    if status_code != 0:
        raise P2PProbeError("alarm-voice catalogue service rejected the read")
    catalog = decode_alarm_voice_catalog(payload)
    if catalog is None or catalog.code != 0:
        raise P2PProbeError("alarm-voice catalogue returned invalid metadata")
    return catalog


def read_camera_alarm_voice_catalog(
    enrollment: P2PEnrollment,
    *,
    language: str = "pt-BR",
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PAlarmVoiceCatalog:
    """Read system and custom type-4 resources without selecting or playing any of them."""

    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(10.0, min(float(total_timeout), 40.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, _target, sequence = open_camera_session(
            sock,
            enrollment,
            bounded_timeout,
            deadline,
        )
        system_result = exchange_alarm_voice_catalog(
            sock,
            node,
            build_alarm_voice_query(
                system=True,
                language=language,
                access_id=enrollment.access_id,
            ),
            sequence,
            bounded_timeout,
            deadline=deadline,
        )
        system = _decode_response(system_result.payload, system_result.status_code)
        custom_result = exchange_alarm_voice_catalog(
            sock,
            node,
            build_alarm_voice_query(
                system=False,
                language=language,
                access_id=enrollment.access_id,
            ),
            (sequence + 1) & 0xFFFFFFFF,
            bounded_timeout,
            deadline=deadline,
        )
        custom = _decode_response(custom_result.payload, custom_result.status_code)
    except P2PProbeError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise P2PProbeError("P2P alarm-voice catalogue read failed") from exc
    finally:
        sock.close()

    resources = system.resources + custom.resources
    if len({resource.key for resource in resources}) != len(resources):
        raise P2PProbeError("alarm-voice catalogue contains conflicting semantic options")
    return P2PAlarmVoiceCatalog(
        enrollment.device_id,
        system.reported_total,
        custom.reported_total,
        system_result.transport_acknowledged and custom_result.transport_acknowledged,
        resources,
    )
