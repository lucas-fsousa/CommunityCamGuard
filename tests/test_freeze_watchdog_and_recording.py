"""Backend support for the freeze watchdog (Go2rtc.stream_activity + the /media/activity route)
and a guard that recording always uses the base (main, highest-quality) feed.
"""
import json
from types import SimpleNamespace

from backend.app.api import routes
from backend.app.db.registry import Camera
from backend.app.media import go2rtc

_STREAMS = {
    "cam_x_hd": {
        "producers": [{"receivers": [
            {"codec": {"codec_type": "video"}, "packets": 1200},
            {"codec": {"codec_type": "audio"}, "packets": 300},   # audio must NOT count
        ]}],
        "consumers": [{"id": 1}],
    },
    "cam_x_web": {   # not watched
        "producers": [{"receivers": [{"codec": {"codec_type": "video"}, "packets": 0}]}],
        "consumers": [],
    },
}


class _Resp:
    def __init__(self, body=b"", status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_stream_activity_counts_video_packets_and_consumers(monkeypatch):
    monkeypatch.setattr(go2rtc.urllib.request, "urlopen",
                        lambda url, timeout=None: _Resp(json.dumps(_STREAMS).encode()))
    out = go2rtc.Go2rtc(manage=False).stream_activity()
    assert out["cam_x_hd"] == {"video_packets": 1200, "consumers": 1}   # audio packets excluded
    assert out["cam_x_web"] == {"video_packets": 0, "consumers": 0}


def test_stream_activity_empty_on_go2rtc_error(monkeypatch):
    def boom(url, timeout=None):
        raise OSError("go2rtc down")
    monkeypatch.setattr(go2rtc.urllib.request, "urlopen", boom)
    assert go2rtc.Go2rtc(manage=False).stream_activity() == {}


def test_media_activity_route_proxies_the_monitor():
    media = SimpleNamespace(stream_activity=lambda: {"cam_x": {"video_packets": 5, "consumers": 2}})
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=media)))
    assert routes.media_activity(req)["cam_x"]["consumers"] == 2


def test_media_activity_route_empty_without_media():
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert routes.media_activity(req) == {}


def test_media_client_event_captures_server_counter_snapshot():
    routes._client_media_events.clear()
    media = SimpleNamespace(stream_activity=lambda: {
        "cam_x_hd": {"video_packets": 321, "consumers": 1},
    })
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=media)))
    body = routes.MediaClientEventIn(
        event="catchup_start", mac="aa:bb:cc:dd:ee:01", stream="cam_x_hd",
        metrics={"bufferedGap": 2.4, "playbackRate": 1.25, "transport": "mse"},
    )
    assert routes.media_client_event(body, req) == {"ok": True}
    event = routes.media_client_events()[-1]
    assert event["server"] == {"video_packets": 321, "consumers": 1}
    assert event["metrics"]["transport"] == "mse"
    assert event["at"].endswith("+00:00")


def test_restart_preload_cycles_only_requested_local_stream(monkeypatch):
    calls = []

    def open_(request, timeout=None):
        method = request.get_method() if hasattr(request, "get_method") else "GET"
        url = request.full_url if hasattr(request, "full_url") else request
        calls.append((method, url))
        if method == "PUT":
            raise go2rtc.urllib.error.HTTPError(url, 500, "go2rtc quirk", {}, None)
        if method == "GET":
            return _Resp(json.dumps({"cam_x_hd": {"query": "video&audio"}}).encode())
        return _Resp()

    monkeypatch.setattr(go2rtc.urllib.request, "urlopen", open_)
    monkeypatch.setattr(go2rtc.time, "sleep", lambda _seconds: None)
    assert go2rtc.Go2rtc(manage=False).restart_preload("cam_x_hd") is True
    assert [method for method, _ in calls] == ["DELETE", "PUT", "GET"]
    assert all("cam_x_hd" in url for _, url in calls[:2])


def test_media_recover_targets_hd_preload_not_camera_source(monkeypatch):
    cam = Camera(mac="aa:bb:cc:dd:ee:01", last_ip="10.0.0.5", stream_path="/onvif1")
    restarted = []
    media = SimpleNamespace(restart_preload=lambda sid: restarted.append(sid) or True)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=media)))
    monkeypatch.setattr(routes.registry, "get_camera", lambda _mac: cam)
    assert routes.media_recover(cam.mac, req) == {"ok": True}
    assert restarted == [go2rtc.hd_stream_id(cam.mac)]


# --- rule: recording always uses the base (main) feed, at full quality --------------

def test_recording_source_is_the_base_main_stream_not_a_transcode():
    """The recorder records from the base restream (the camera's main feed, -c:v copy), never the
    dashboard's `_web`/`_hd`/`_sub` quality variants — recording stays at the highest quality
    regardless of what the live view is set to."""
    mac = "aa:bb:cc:dd:ee:01"
    url = go2rtc.restream_rtsp_url(mac)
    assert url.endswith(f"/{go2rtc.stream_id(mac)}")      # base stream id
    for suffix in ("_web", "_hd", "_sub"):
        assert not url.endswith(suffix)


def test_base_stream_is_the_cameras_main_rtsp_url():
    cam = Camera(mac="aa:bb:cc:dd:ee:01", last_ip="10.0.0.5", stream_path="/onvif1",
                 capabilities={"stream_paths": ["/onvif1", "/onvif2"]})
    streams = go2rtc.build_config(cameras=[cam])["streams"]
    # the base stream (what the recorder copies) is the MAIN feed /onvif1, not the /onvif2 substream
    assert streams[go2rtc.stream_id(cam.mac)] == cam.rtsp_url
    assert streams[go2rtc.stream_id(cam.mac)].endswith("/onvif1")
