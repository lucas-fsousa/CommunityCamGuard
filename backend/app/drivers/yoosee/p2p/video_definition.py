"""SDK-backed video-definition payloads only; no send path or capability grant."""

import struct
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DefinitionRequest:
    command: int
    payload: bytes


def encode_definitions(platform_version: int, definitions: Mapping[int, int]) -> DefinitionRequest:
    """Explicit authoritative platform; LD=1, SD=2, HD=3, AUTO=7.

    Platform 1 has a global byte. Reject conflicting channel requests instead of
    silently taking the SDK's first map entry. Platform 2 packs up to five slots.
    An HD enum does not prove a device's maximum resolution or feature support.
    """
    if type(platform_version) is not int or platform_version not in (1, 2):
        raise ValueError("video definition requires a known device platform")
    if not 1 <= len(definitions) <= 5:
        raise ValueError("video definitions require one to five explicit channels")
    for channel, definition in definitions.items():
        if type(channel) is not int or not 0 <= channel < 5:
            raise ValueError("unsupported video-definition channel")
        if type(definition) is not int or definition not in (1, 2, 3, 7):
            raise ValueError("unsupported video definition")
    if platform_version == 1:
        values = set(definitions.values())
        if len(values) != 1:
            raise ValueError("legacy video definition is global")
        return DefinitionRequest(5, bytes((values.pop() - 1,)))
    packed = sum(value << (3 * channel) for channel, value in definitions.items())
    return DefinitionRequest(0x33, struct.pack("<H", packed))


def with_startup_definitions(user_data: bytes, platform_version: int,
                             definitions: Mapping[int, int]) -> bytes:
    """Copy reviewed 32-byte userdata, replacing only its platform's quality field.

    SDK LivePlayer stores legacy quality at userdata[0] and packed quality at
    userdata[23:25]. Preserve every other field, including the other platform's
    quality. No default template, connection-type inference or transmission.
    Sparse slot maps have the same zero-slot caveat as encode_definitions.
    """
    if not isinstance(user_data, bytes) or len(user_data) != 32:
        raise ValueError("startup video definition requires 32-byte userdata")
    request = encode_definitions(platform_version, definitions)
    result = bytearray(user_data)
    offset = 0 if request.command == 5 else 23
    result[offset:offset + len(request.payload)] = request.payload
    return bytes(result)
