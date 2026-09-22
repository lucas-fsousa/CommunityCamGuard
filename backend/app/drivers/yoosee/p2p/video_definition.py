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
