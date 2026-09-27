"""Client telemetry and support snapshots never carry arbitrary diagnostic text."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api import media
from backend.app.db import registry
from backend.app.media.diagnostic_fields import public_metrics
from scripts import watch_live_streams as watcher

SECRET = "SYNTHETIC_TELEMETRY_SECRET"


def test_metrics_preserve_known_numeric_data_not_text_or_unknown_keys():
    result = public_metrics({"bufferedGap": 3.2, "transport": "mse", "rtcPacketsReceived": 12,
        "error": SECRET, SECRET: 2, "mseQueueBytes": SECRET, "paused": True,
        "currentTime": float("nan"), "playbackRate": 10**1000})
    assert result == {"bufferedGap": 3.2, "transport": "mse", "rtcPacketsReceived": 12,
                      "paused": True, "error_present": True}


def test_client_event_logs_only_projection(caplog):
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=SimpleNamespace(
        stream_activity=lambda: {camera.camera_id + "_hd": {"video_packets": 3, "consumers": 1,
                                                            "token": SECRET}}))))
    body = media.MediaClientEventIn(event="mse_failure", camera_id=camera.camera_id,
        stream=camera.camera_id + "_hd", metrics={"error": SECRET, SECRET: SECRET})
    assert media.media_client_event(body, req) == {"ok": True}
    assert SECRET not in json.dumps(media.media_client_events()[-1]) + caplog.text
    body.stream = SECRET
    with pytest.raises(HTTPException) as caught:
        media.media_client_event(body, req)
    assert caught.value.status_code == 422


def test_watcher_refilters_historical_events(monkeypatch):
    camera = "cam_" + "a" * 24
    event = {"event": "stalled", "camera_id": camera, "stream": camera + "_hd",
             "metrics": {"error": SECRET, "bufferedGap": 2}, "extra": SECRET}
    monkeypatch.setattr(watcher, "_run", lambda *args, **kwargs:
                        (0, "live_view_event " + json.dumps(event)))
    result = watcher.docker_client_events("ccg-app", "synthetic")
    assert result[0]["metrics"] == {"bufferedGap": 2, "error_present": True}
    assert SECRET not in str(result)


def test_watcher_does_not_echo_command_failures(monkeypatch):
    monkeypatch.setattr(watcher, "_run", lambda *args, **kwargs: (1, SECRET))
    assert SECRET not in str(watcher.docker_stats(("ccg-app",)))
    assert SECRET not in str(watcher.docker_states(("ccg-app",)))
    assert SECRET not in str(watcher.docker_processes("ccg-app"))


def test_watcher_omits_unknown_stream_names_and_urls(monkeypatch):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == 2 * 1024 * 1024 + 1
            return json.dumps({SECRET: {"url": SECRET}, "cam_" + "a" * 24: {
                "producers": [], "consumers": [], "url": SECRET}}).encode()

    monkeypatch.setattr(watcher.urllib.request, "urlopen", lambda *args, **kwargs: Reply())
    result = watcher.go2rtc_streams("http://localhost:3201")
    assert SECRET not in str(result)
    assert result["cam_" + "a" * 24]["video_packets"] == 0
