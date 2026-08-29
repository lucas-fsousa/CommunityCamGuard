"""Sanitized alarm-voice catalogue contracts recovered from the Yoosee APK.

This module is intentionally socket-free.  It decodes only type-4 catalogue metadata, retains the
vendor resource id as a private non-repr field for a future typed selector, and never retains
signed download/upload URLs.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
from dataclasses import dataclass, field

ALARM_VOICE_RESOURCE_TYPE = 4
_OPTION_KEY = re.compile(r"^(system|custom)-([0-9]{1,10})$")
_LANGUAGE_IDS = {
    "en": 1,
    "zh-cn": 2,
    "th": 3,
    "vi": 4,
    "de": 5,
    "ko": 6,
    "fr": 7,
    "pt": 8,
    "it": 9,
    "ru": 10,
    "ja": 11,
    "es": 12,
    "pl": 13,
    "tr": 14,
    "fa": 15,
    "id": 16,
    "in": 16,
    "ms": 17,
    "cs": 18,
    "sk": 19,
    "nl": 20,
    "zh-tw": 21,
    "el": 22,
    "gr": 22,
}


@dataclass(frozen=True, slots=True)
class AlarmVoiceResource:
    """One validated internal catalogue entry; credentials and signed URLs are absent."""

    key: str
    name: str
    duration_ms: int | None
    audio_format: str
    system: bool
    logical_number: int
    resource_id: str = field(repr=False)

    def public(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.name,
            "duration_ms": self.duration_ms,
            "system": self.system,
        }


@dataclass(frozen=True, slots=True)
class AlarmVoiceCatalog:
    code: int
    reported_total: int
    resources: tuple[AlarmVoiceResource, ...]


def alarm_voice_language_keyword(language: str) -> str:
    """Translate a locale to the APK's resource-language keyword."""

    if not isinstance(language, str):
        raise ValueError("alarm-voice language must be a string")
    normalized = language.strip().lower().replace("_", "-")
    if normalized.startswith("zh-"):
        lookup = "zh-cn" if normalized in {"zh-cn", "zh-hans"} else "zh-tw"
    else:
        lookup = normalized.split("-", 1)[0]
    return f"language_{_LANGUAGE_IDS.get(lookup, 1)}"


def build_alarm_voice_query(
    *,
    system: bool,
    language: str = "pt-BR",
    access_id: int,
) -> dict[str, object]:
    """Build only the read-only ``resfile/queryres`` body recovered from the APK."""

    if type(system) is not bool or type(access_id) is not int:
        raise ValueError("alarm-voice query requires a boolean source and integer access id")
    signed_access_id = access_id & 0xFFFFFFFFFFFFFFFF
    if signed_access_id >= 1 << 63:
        signed_access_id -= 1 << 64
    query: dict[str, object] = {
        "pageSize": 20,
        "curPage": 0,
        "resTypes": [ALARM_VOICE_RESOURCE_TYPE],
        "bySys": int(system),
        "accessId": str(signed_access_id),
    }
    if system:
        query["keyWord"] = alarm_voice_language_keyword(language)
    return query


def alarm_voice_logical_number(resource_id: object) -> int | None:
    """Extract the stable logical number from one opaque, valid type-4 resource id."""

    if not isinstance(resource_id, str):
        return None
    try:
        decoded = base64.b64decode(resource_id, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) != 24:
        return None
    resource_type, logical_number = struct.unpack_from("<II", decoded)
    if resource_type != ALARM_VOICE_RESOURCE_TYPE:
        return None
    return logical_number


def parse_alarm_voice_option_key(key: object) -> tuple[bool, int] | None:
    """Decode only a server-issued semantic key, never a vendor resource id."""

    if not isinstance(key, str):
        return None
    matched = _OPTION_KEY.fullmatch(key)
    if matched is None:
        return None
    return matched.group(1) == "system", int(matched.group(2))


def _description(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def decode_alarm_voice_catalog(payload: bytes | None) -> AlarmVoiceCatalog | None:
    """Decode a catalogue response while discarding URL/token-bearing fields."""

    if payload is None or len(payload) > 1_000_000:
        return None
    try:
        root = json.loads(payload.decode("utf-8"))
        if not isinstance(root, dict):
            return None
        code = root.get("code", 0)
        data = root.get("data")
        if isinstance(data, str):
            data = json.loads(data)
        if type(code) is not int or not isinstance(data, dict):
            return None
        total = data.get("total", 0)
        entries = data.get("urls", [])
        if type(total) is not int or total < 0 or not isinstance(entries, list):
            return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    resources: list[AlarmVoiceResource] = []
    seen: set[str] = set()
    for entry in entries[:20]:
        if not isinstance(entry, dict) or entry.get("resType") != ALARM_VOICE_RESOURCE_TYPE:
            continue
        resource_id = entry.get("resId")
        logical_number = alarm_voice_logical_number(resource_id)
        if logical_number is None or not isinstance(resource_id, str):
            continue
        system_raw = entry.get("isSys")
        if type(system_raw) is not int or system_raw not in (0, 1):
            continue
        description = _description(entry.get("desc"))
        name = description.get("name") or description.get("alias")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()[:120]
        audio_format = description.get("audioFormat")
        if not isinstance(audio_format, str) or audio_format.lower() != "amr":
            continue
        duration = description.get("duration")
        if type(duration) is not int or not 0 <= duration <= 60_000:
            duration = None
        system = bool(system_raw)
        key = f"{'system' if system else 'custom'}-{logical_number}"
        if key in seen:
            continue
        seen.add(key)
        resources.append(
            AlarmVoiceResource(
                key,
                name,
                duration,
                "AMR",
                system,
                logical_number,
                resource_id,
            )
        )
    return AlarmVoiceCatalog(code, total, tuple(resources))


def find_alarm_voice_resource(
    catalogs: tuple[AlarmVoiceCatalog, ...], option_key: str
) -> AlarmVoiceResource | None:
    """Resolve one semantic option only against freshly decoded catalogue entries."""

    parsed = parse_alarm_voice_option_key(option_key)
    if parsed is None:
        return None
    system, logical_number = parsed
    return next(
        (
            resource
            for catalog in catalogs
            for resource in catalog.resources
            if resource.system is system and resource.logical_number == logical_number
        ),
        None,
    )
