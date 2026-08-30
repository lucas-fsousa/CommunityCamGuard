"""Typed persistent alarm-voice selection using only a validated catalogue resource.

The Yoosee semantic-control adapter always resolves a public option key through a fresh sanitized
catalogue before entering this module; callers cannot supply a raw vendor resource ID.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from ....db.p2p import P2PEnrollment
from .alarm_voice import AlarmVoiceResource, alarm_voice_logical_number
from .camera_session import open_camera_session
from .contracts import CertifiedNode, ModelWriteResult, OnlineDevice, P2PProbeError
from .model_session import exchange_model_read
from .model_write_protocol import build_model_write, parse_model_write_response
from .model_write_session import exchange_model_write

ALARM_VOICE_READ_PATH = "ProWritable.resFile"
ALARM_VOICE_WRITE_PATH = "ProWritable.resFile.setVal.resId"


@dataclass(frozen=True, slots=True)
class AlarmVoiceSelectionState:
    logical_number: int
    support_state: int


@dataclass(frozen=True, slots=True)
class P2PAlarmVoiceWrite:
    device_id: str
    option_key: str
    previous_logical_number: int
    requested_logical_number: int
    changed: bool
    transport_acknowledged: bool
    error_code: int | None
    verified: bool


def extract_alarm_voice_selection(value: object) -> AlarmVoiceSelectionState | None:
    """Extract the current type-4 selection and explicit support state from ``resFile``."""

    if not isinstance(value, dict):
        return None
    resource_id = value.get("resId")
    support = value.get("supportFunc")
    logical_number = alarm_voice_logical_number(resource_id)
    if logical_number is not None and type(support) is int and support in (1, 2, 3):
        return AlarmVoiceSelectionState(logical_number, support)
    for key in ("setVal", "resFile", "ProWritable"):
        if key in value:
            candidate = extract_alarm_voice_selection(value[key])
            if candidate is not None:
                return candidate
    return None


def build_alarm_voice_selection_write(
    node: CertifiedNode,
    device_id: int,
    resource: AlarmVoiceResource,
    sequence: int,
    message_id: int,
) -> bytes:
    """Build the fixed selection leaf from a catalogue-validated resource only."""

    if not isinstance(resource, AlarmVoiceResource):
        raise ValueError("alarm voice must come from a validated catalogue")
    if alarm_voice_logical_number(resource.resource_id) != resource.logical_number:
        raise ValueError("alarm voice catalogue resource is internally inconsistent")
    return build_model_write(
        node,
        device_id,
        ALARM_VOICE_WRITE_PATH,
        resource.resource_id,
        sequence,
        message_id,
    )


def parse_alarm_voice_selection_response(frame: bytes, message_id: int) -> int | None:
    return parse_model_write_response(frame, message_id)


def exchange_alarm_voice_selection_write(
    sock: socket.socket,
    node: CertifiedNode,
    device: OnlineDevice,
    resource: AlarmVoiceResource,
    sequence: int,
    timeout: float,
    *,
    retries: int = 3,
    deadline: float | None = None,
) -> ModelWriteResult:
    if not isinstance(resource, AlarmVoiceResource):
        raise ValueError("alarm voice must come from a validated catalogue")
    return exchange_model_write(
        sock,
        node,
        device,
        ALARM_VOICE_WRITE_PATH,
        resource.resource_id,
        sequence,
        timeout,
        retries=retries,
        deadline=deadline,
    )


def set_camera_alarm_voice_resource(
    enrollment: P2PEnrollment,
    resource: AlarmVoiceResource,
    *,
    timeout: float = 1.5,
    total_timeout: float = 30.0,
) -> P2PAlarmVoiceWrite:
    """Select a validated resource with preflight and logical-number readback."""

    if not isinstance(resource, AlarmVoiceResource):
        raise ValueError("alarm voice must come from a validated catalogue")
    bounded_timeout = max(0.5, min(float(timeout), 5.0))
    deadline = time.monotonic() + max(10.0, min(float(total_timeout), 40.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    try:
        node, target, sequence = open_camera_session(sock, enrollment, bounded_timeout, deadline)
        preflight = exchange_model_read(
            sock,
            node,
            target,
            ALARM_VOICE_READ_PATH,
            sequence,
            min(5.0, max(0.5, deadline - time.monotonic())),
            deadline=deadline,
        )
        previous = extract_alarm_voice_selection(preflight.value)
        if preflight.error_code != 0 or previous is None:
            raise P2PProbeError("alarm-voice preflight returned no supported type-4 selection")
        if previous.logical_number == resource.logical_number:
            return P2PAlarmVoiceWrite(
                enrollment.device_id,
                resource.key,
                previous.logical_number,
                resource.logical_number,
                False,
                False,
                0,
                True,
            )
        write = exchange_alarm_voice_selection_write(
            sock,
            node,
            target,
            resource,
            (sequence + 1) & 0xFFFFFFFF,
            bounded_timeout,
            deadline=deadline,
        )
        if write.error_code != 0:
            raise P2PProbeError("camera rejected the alarm-voice selection")
        verified = False
        for attempt in range(5):
            if attempt:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.5, remaining))
            readback = exchange_model_read(
                sock,
                node,
                target,
                ALARM_VOICE_READ_PATH,
                (sequence + 2 + attempt) & 0xFFFFFFFF,
                min(bounded_timeout, max(0.5, deadline - time.monotonic())),
                retries=1,
                deadline=deadline,
            )
            current = extract_alarm_voice_selection(readback.value)
            if (
                readback.error_code == 0
                and current is not None
                and current.logical_number == resource.logical_number
            ):
                verified = True
                break
        if not verified:
            raise P2PProbeError("camera did not confirm the alarm-voice selection")
    except P2PProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise P2PProbeError("P2P alarm-voice selection failed") from exc
    finally:
        sock.close()
    return P2PAlarmVoiceWrite(
        enrollment.device_id,
        resource.key,
        previous.logical_number,
        resource.logical_number,
        True,
        write.transport_acknowledged,
        write.error_code,
        True,
    )
