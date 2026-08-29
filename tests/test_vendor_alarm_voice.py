from __future__ import annotations

import base64
import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p.alarm_voice import (
    alarm_voice_language_keyword,
    alarm_voice_logical_number,
    build_alarm_voice_query,
    decode_alarm_voice_catalog,
    find_alarm_voice_resource,
    parse_alarm_voice_option_key,
)


def _resource_id(number: int, opaque: int = 10) -> str:
    return base64.b64encode(struct.pack("<IIQQ", 4, number, 0, opaque)).decode()


def _catalog_payload(*entries: dict[str, object]) -> bytes:
    return json.dumps({"code": 0, "data": {"total": len(entries), "urls": list(entries)}}).encode()


def test_alarm_voice_query_is_read_only_localized_and_uses_signed_access_id():
    assert alarm_voice_language_keyword("pt-BR") == "language_8"
    assert alarm_voice_language_keyword("zh_CN") == "language_2"
    assert alarm_voice_language_keyword("th-TH") == "language_3"
    assert alarm_voice_language_keyword("de-DE") == "language_5"
    assert alarm_voice_language_keyword("es-ES") == "language_12"
    assert build_alarm_voice_query(
        system=True,
        language="pt-BR",
        access_id=0x800000000000007B,
    ) == {
        "pageSize": 20,
        "curPage": 0,
        "resTypes": [4],
        "bySys": 1,
        "accessId": "-9223372036854775685",
        "keyWord": "language_8",
    }


def test_catalog_exposes_semantic_keys_but_retains_no_url_or_token():
    resource_id = _resource_id(7)
    catalog = decode_alarm_voice_catalog(
        _catalog_payload(
            {
                "resType": 4,
                "isSys": 1,
                "resId": resource_id,
                "url": "https://must-not-survive.invalid/audio?token=secret",
                "uploadToken": "must-not-survive",
                "desc": json.dumps(
                    {
                        "name": "Latido",
                        "audioFormat": "amr",
                        "duration": 8000,
                        "num": 7,
                    }
                ),
            }
        )
    )

    assert catalog is not None
    assert catalog.resources[0].public() == {
        "key": "system-7",
        "label": "Latido",
        "duration_ms": 8000,
        "system": True,
    }
    assert resource_id not in repr(catalog.resources[0])
    assert "must-not-survive" not in repr(catalog)


def test_catalog_filters_wrong_type_format_invalid_ids_and_duplicate_keys():
    valid_id = _resource_id(3)
    catalog = decode_alarm_voice_catalog(
        _catalog_payload(
            {
                "resType": 4,
                "isSys": 0,
                "resId": valid_id,
                "desc": {"name": "Custom", "audioFormat": "AMR", "duration": 1000},
            },
            {
                "resType": 4,
                "isSys": 0,
                "resId": _resource_id(3, 99),
                "desc": {"name": "Duplicate", "audioFormat": "AMR"},
            },
            {
                "resType": 6,
                "isSys": 1,
                "resId": base64.b64encode(struct.pack("<IIQQ", 6, 4, 0, 1)).decode(),
                "desc": {"name": "AI model", "audioFormat": "AMR"},
            },
            {
                "resType": 4,
                "isSys": 1,
                "resId": valid_id,
                "desc": {"name": "Wrong codec", "audioFormat": "AAC"},
            },
        )
    )

    assert catalog is not None
    assert [item.key for item in catalog.resources] == ["custom-3"]


@pytest.mark.parametrize("value", [None, "opaque-id", _resource_id(1)[:-2], 3])
def test_resource_id_requires_exact_opaque_type_four_shape(value):
    assert alarm_voice_logical_number(value) is None


def test_selection_key_resolves_only_against_decoded_catalog():
    catalog = decode_alarm_voice_catalog(
        _catalog_payload(
            {
                "resType": 4,
                "isSys": 1,
                "resId": _resource_id(9),
                "desc": {"name": "Bip", "audioFormat": "AMR"},
            }
        )
    )
    assert catalog is not None

    selected = find_alarm_voice_resource((catalog,), "system-9")

    assert selected is not None and selected.name == "Bip"
    assert find_alarm_voice_resource((catalog,), "system-8") is None
    assert parse_alarm_voice_option_key(_resource_id(9)) is None
    assert parse_alarm_voice_option_key("../../native") is None
