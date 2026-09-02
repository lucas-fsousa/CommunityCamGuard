from datetime import UTC, datetime, timedelta

import pytest

from backend.app.db.registry import Camera
from backend.app.drivers.base import CameraDriver, Unsupported
from backend.app.drivers.contracts import (
    OnboardRecording,
    OnboardRecordingPage,
    OnboardRecordingQuery,
)

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_onboard_recording_contract_projects_only_generic_utc_fields():
    item = OnboardRecording("c29tZS1vcGFxdWU", START, START + timedelta(seconds=30), "motion")
    page = OnboardRecordingPage((item,), next_cursor="bmV4dC1wYWdl", total=2)

    assert page.public() == {
        "items": [
            {
                "id": "c29tZS1vcGFxdWU",
                "started_at": "2026-09-01T12:00:00Z",
                "ended_at": "2026-09-01T12:00:30Z",
                "duration_ms": 30_000,
                "kind": "motion",
                "size_bytes": None,
            }
        ],
        "next_cursor": "bmV4dC1wYWdl",
        "total": 2,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_utc": START.replace(tzinfo=None), "end_utc": START + timedelta(hours=1)},
        {"start_utc": START, "end_utc": START},
        {"start_utc": START, "end_utc": START + timedelta(hours=1), "limit": 201},
        {"start_utc": START, "end_utc": START + timedelta(hours=1), "cursor": "raw/path"},
    ],
)
def test_onboard_query_rejects_ambiguous_or_unbounded_values(kwargs):
    with pytest.raises(ValueError):
        OnboardRecordingQuery(**kwargs)


def test_onboard_item_rejects_local_time_raw_paths_and_invalid_ranges():
    with pytest.raises(ValueError):
        OnboardRecording("vendor/raw/path", START, START + timedelta(seconds=1))
    with pytest.raises(ValueError):
        OnboardRecording("opaque", START.replace(tzinfo=None), START + timedelta(seconds=1))
    with pytest.raises(ValueError):
        OnboardRecording("opaque", START, START)
    with pytest.raises(ValueError):
        OnboardRecording("opaque", START, START + timedelta(seconds=1), size_bytes=-1)


def test_default_driver_never_lists_onboard_recordings():
    driver = CameraDriver()
    camera = Camera(mac="aa:bb:cc:dd:ee:01")
    query = OnboardRecordingQuery(START, START + timedelta(hours=1))

    with pytest.raises(Unsupported):
        driver.list_onboard_recordings(camera, query)
