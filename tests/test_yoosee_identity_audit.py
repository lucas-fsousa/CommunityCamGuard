import json
import sqlite3

import pytest

from scripts.audit_yoosee_identity import PREFIX, associations, audit, parse_line


def callback():
    return (
        PREFIX
        + json.dumps(
            {
                "_productInfo": {"productID": "123", "productModel": "model", "revision": 1},
                "_versionInfo": {"swVer": "1", "sdkVer": "2", "hwVer": ""},
                "secret": "must-not-escape",
            }
        )
        + " | ProConst | 7000000002\n"
    )


def test_extracts_only_complete_explicit_identity():
    assert parse_line(callback()).revision == 1
    assert parse_line(callback().replace(" | ProConst |", " | ProWritable |")) is None
    assert parse_line(callback().replace("7000000002", "")) is None
    assert parse_line(PREFIX + "{} | ProConst | 7000000002") is None


def test_audit_redacts_raw_identity_and_unrelated_fields(tmp_path):
    path = tmp_path / "capture.log"
    path.write_text(callback())
    records = list(audit(path, {"7000000002": "cam_" + "1" * 24}))
    encoded = json.dumps(records)
    assert "7000000002" not in encoded and "must-not-escape" not in encoded
    assert records[0]["line"] == 1 and records[0]["historical_only"] is True
    assert records[0]["camera_id"] == "cam_" + "1" * 24


def test_refuses_oversized_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.audit_yoosee_identity.MAX_LINE", 10)
    path = tmp_path / "capture.log"
    path.write_text("x" * 11)
    with pytest.raises(ValueError, match="budget"):
        list(audit(path, {}))


def test_read_only_database_never_creates_missing_database(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        associations(path)
    assert not path.exists()
