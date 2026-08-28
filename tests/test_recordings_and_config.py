from pathlib import Path

import pytest

from backend.app import config
from backend.app.api import cameras as camera_routes
from backend.app.api import media as media_routes
from backend.app.api import recordings as recording_routes
from backend.app.api import routes
from backend.app.camera_identity import stable_camera_id
from backend.app.db import connect, registry
from backend.app.db.registry import Camera
from backend.app.media import go2rtc, quality
from backend.app.recording import recorder


def _camera(mac: str, **kwargs) -> Camera:
    return Camera(mac=mac, camera_id=stable_camera_id("mac", mac), **kwargs)


def _raw(*, hd: bool, has_audio: bool = True) -> str:
    """The expected go2rtc ``#raw=`` chunk under the current settings — computed, not hardcoded,
    so these wiring tests stay correct if the quality bitrates are ever retuned."""
    s = config.get_settings()
    return quality.encode_raw_args(
        s.live_quality,
        s.live_fps,
        hd=hd,
        repair_audio_clock=has_audio,
    )


def _seed(n, mac="aabbccddeeff", camera_id="", day="2026-07-27"):
    recorder.init_db()
    with connect() as c:
        c.execute("DELETE FROM recordings")
        c.executemany(
            "INSERT INTO recordings "
            "(mac,camera_id,path,started_at,day,hour,size_bytes,duration_s,indexed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (mac, camera_id, f"/r/{i}.mp4", f"{day}T00:{i:02d}:00", day, 0, 1000, 60, "x")
                for i in range(n)
            ],
        )


def test_query_pagination_and_total():
    _seed(120)
    r = recorder.query_segments(limit=50, offset=0)
    assert r["total"] == 120 and len(r["items"]) == 50 and r["offset"] == 0
    r2 = recorder.query_segments(limit=50, offset=100)
    assert len(r2["items"]) == 20


def test_query_limit_clamped():
    _seed(5)
    assert recorder.query_segments(limit=99999)["limit"] == recorder.MAX_PAGE


def test_query_newest_first():
    _seed(3)
    items = recorder.query_segments()["items"]
    assert items[0]["started_at"] > items[-1]["started_at"]


def test_query_day_range_filter():
    _seed(10, day="2026-07-20")
    assert recorder.query_segments(day_from="2026-07-25", day_to="2026-07-27")["total"] == 0
    assert recorder.query_segments(day_from="2026-07-01", day_to="2026-07-31")["total"] == 10


def test_query_filters_by_opaque_camera_id():
    camera_id = stable_camera_id("mac", "aa:bb:cc:dd:ee:01")
    _seed(3, camera_id=camera_id)
    assert recorder.query_segments(camera_id=camera_id)["total"] == 3
    assert (
        recorder.query_segments(camera_id=stable_camera_id("mac", "aa:bb:cc:dd:ee:02"))["total"]
        == 0
    )


def test_recordings_endpoint_includes_retention_days(monkeypatch):
    registry.init_db()
    _seed(3)
    monkeypatch.setenv("RECORDING_RETENTION_DAYS", "7")
    config.get_settings.cache_clear()
    res = recording_routes.recordings()  # route fn is callable directly (FastAPI)
    assert res["total"] == 3 and res["retention_days"] == 7  # page context for the UI banner
    monkeypatch.setenv("RECORDING_RETENTION_DAYS", "0")
    config.get_settings.cache_clear()
    assert recording_routes.recordings()["retention_days"] == 0  # 0 = kept forever


def test_recordings_endpoint_resolves_safe_mac_to_friendly_camera_name():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:01", name="Front door")
    _seed(1, mac="aabbccddee01", camera_id=camera.camera_id)

    item = recording_routes.recordings()["items"][0]

    assert item["mac"] == "aabbccddee01"
    assert item["camera_id"] == camera.camera_id
    assert item["camera_name"] == "Front door"


def test_recording_schema_backfills_legacy_rows_from_registered_mac():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:01")
    _seed(1, mac="aabbccddee01")

    recorder.init_db()

    assert recorder.query_segments()["items"][0]["camera_id"] == camera.camera_id


def test_recording_schema_migrates_a_pre_camera_id_database():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:01")
    with connect() as connection:
        connection.executescript(
            """CREATE TABLE recordings (
                   id INTEGER PRIMARY KEY, mac TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                   started_at TEXT NOT NULL, day TEXT NOT NULL, hour INTEGER NOT NULL,
                   size_bytes INTEGER NOT NULL, duration_s INTEGER NOT NULL, indexed_at TEXT NOT NULL
               );"""
        )
        connection.execute(
            "INSERT INTO recordings "
            "(mac,path,started_at,day,hour,size_bytes,duration_s,indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "aabbccddee01",
                "/legacy.mp4",
                "2026-08-01T12:00:00+00:00",
                "2026-08-01",
                12,
                100,
                60,
                "x",
            ),
        )

    recorder.init_db()

    with connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(recordings)")}
    assert "camera_id" in columns
    assert recorder.query_segments()["items"][0]["camera_id"] == camera.camera_id


def test_recordings_endpoint_rejects_invalid_camera_id():
    registry.init_db()
    with pytest.raises(recording_routes.HTTPException) as error:
        recording_routes.recordings(camera_id="aa:bb:cc:dd:ee:01")
    assert error.value.status_code == 422


def test_media_streams_reports_quality(monkeypatch):
    from types import SimpleNamespace

    # media_streams reads request.app.state.media; None -> not healthy, no hardware needed.
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=None)))
    monkeypatch.setenv("LIVE_QUALITY", "high")
    config.get_settings.cache_clear()
    out = media_routes.media_streams(req)
    assert out["live_quality"] == "high"
    assert out["quality_levels"] == list(quality.LEVELS)
    assert out["healthy"] is False
    assert "grid_hd_max_cameras" in out


def _add_body(**kw):
    return camera_routes.CameraIn(
        mac="aa:bb:cc:dd:ee:ff",
        name="Cam",
        username="admin",
        password="x",
        stream_path="/onvif1",
        **kw,
    )


def _fake_request():
    from types import SimpleNamespace

    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=None, rec=None)))


def test_add_camera_auto_probes_capabilities(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry
    from backend.app.discovery import active_scan

    registry.init_db()
    monkeypatch.setattr(camera_routes.rtsp, "check_credentials", lambda *a, **k: "ok")
    ports_seen = {}

    class _Caps:
        def to_dict(self):
            return {"driver": "yoosee", "ptz": True, "has_audio": True}

    monkeypatch.setattr(
        active_scan, "enumerate_ports", lambda ip: ports_seen.setdefault("ip", ip) or [554, 5000]
    )
    monkeypatch.setattr(drivers, "probe", lambda cam, ports: _Caps())
    out = camera_routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert out["capabilities"]["ptz"] is True  # probed + stored during add
    assert ports_seen["ip"] == "192.168.1.50"  # probed the camera's IP


def test_add_camera_without_ip_skips_probe(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry

    registry.init_db()
    monkeypatch.setattr(
        drivers, "probe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed"))
    )
    out = camera_routes.upsert_camera(_add_body(last_ip=None), _fake_request())
    assert out["capabilities"] == {}  # no IP → no credential check, no probe, still added


def test_add_camera_probe_failure_is_swallowed(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry
    from backend.app.discovery import active_scan

    registry.init_db()
    monkeypatch.setattr(camera_routes.rtsp, "check_credentials", lambda *a, **k: "ok")
    monkeypatch.setattr(active_scan, "enumerate_ports", lambda ip: [554])
    monkeypatch.setattr(
        drivers, "probe", lambda cam, ports: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = camera_routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"  # camera still added despite probe failure


def test_add_camera_rejects_wrong_credentials(monkeypatch):
    import pytest

    from backend.app.db import registry

    registry.init_db()
    monkeypatch.setattr(
        camera_routes.rtsp, "check_credentials", lambda *a, **k: "auth"
    )  # camera rejects creds
    with pytest.raises(routes.HTTPException) as ei:
        camera_routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert (
        ei.value.status_code == 422
    )  # 422 (validation), NOT 401 — 401 would bounce the UI to login
    assert registry.get_camera("aa:bb:cc:dd:ee:ff") is None  # rejected → not saved


def test_add_camera_unreachable_is_allowed(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry
    from backend.app.discovery import active_scan

    registry.init_db()
    monkeypatch.setattr(
        camera_routes.rtsp, "check_credentials", lambda *a, **k: "unreachable"
    )  # offline, ambiguous
    monkeypatch.setattr(active_scan, "enumerate_ports", lambda ip: [554])
    monkeypatch.setattr(
        drivers, "probe", lambda cam, ports: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    out = camera_routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"  # unreachable ≠ wrong password → still added


def test_go2rtc_restart_managed_reexecs_binary(monkeypatch):
    g = go2rtc.Go2rtc(manage=True)
    calls = []
    monkeypatch.setattr(g, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(g, "start", lambda cams=None: calls.append("start"))
    monkeypatch.setattr(g, "reload_external", lambda: calls.append("reload"))
    g.restart()
    assert calls == ["stop", "start"]  # owns the binary → re-exec, no API reload


def test_go2rtc_restart_external_reloads_never_spawns(monkeypatch, tmp_path):
    from backend.app.db import registry

    registry.init_db()  # write_config reads the (empty) cameras table
    g = go2rtc.Go2rtc(manage=False, config_path=tmp_path / "g.yaml")
    calls = []
    monkeypatch.setattr(g, "start", lambda cams=None: calls.append("start"))  # must NOT be called
    monkeypatch.setattr(g, "reload_external", lambda: calls.append("reload") or True)
    g.restart()
    assert calls == ["reload"]  # external → config reload, never spawns a binary
    assert (tmp_path / "g.yaml").exists()  # config regenerated for the external go2rtc


def test_reload_external_posts_restart(monkeypatch):
    seen = {}

    class _R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        return _R()

    monkeypatch.setattr(go2rtc.urllib.request, "urlopen", fake_urlopen)
    assert go2rtc.Go2rtc(manage=False).reload_external() is True
    assert seen["url"].endswith("/api/restart") and seen["method"] == "POST"


def test_reload_external_false_on_transport_error(monkeypatch):
    monkeypatch.setattr(
        go2rtc.urllib.request,
        "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(OSError("down")),
    )
    assert go2rtc.Go2rtc(manage=False).reload_external() is False


def test_resync_is_best_effort_on_media_error():
    from types import SimpleNamespace

    class _Media:
        def restart(self):
            raise RuntimeError("boom")

        def wait_healthy(self, timeout):
            return False

    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=_Media(), rec=None)))
    camera_routes.resync_services(req)  # must not raise — CRUD op stays successful


def test_go2rtc_config_binds_loopback():
    cfg = go2rtc.build_config(cameras=[])
    for section in ("api", "rtsp", "webrtc"):
        listen = cfg[section]["listen"]
        assert listen.startswith("127.0.0.1:")


def test_go2rtc_suppresses_modules_that_log_plaintext_source_credentials():
    cfg = go2rtc.build_config(cameras=[])
    assert cfg["log"] == {
        "level": "info",
        "streams": "disabled",
        "exec": "error",
    }


def test_stream_id_and_restream_url():
    camera_id = stable_camera_id("mac", "aa:bb:cc:dd:ee:01")
    assert go2rtc.stream_id(camera_id) == camera_id
    assert go2rtc.web_stream_id(camera_id) == f"{camera_id}_web"
    assert go2rtc.restream_rtsp_url(camera_id).endswith(f"/{camera_id}")


def test_media_helpers_reject_driver_native_identifiers():
    with pytest.raises(ValueError, match="opaque camera_id"):
        go2rtc.stream_id("aa:bb:cc:dd:ee:01")


def test_go2rtc_web_variant_exists_for_every_camera_audio_only_changes_the_track():
    silent = _camera("aa:bb:cc:00:00:01", stream_path="/onvif1", last_ip="10.0.0.1")
    audible = _camera(
        "aa:bb:cc:00:00:02",
        stream_path="/onvif1",
        last_ip="10.0.0.2",
        capabilities={"has_audio": True},
    )
    streams = go2rtc.build_config(cameras=[silent, audible])["streams"]
    # The live variant is about **video**, not audio: the browser cannot play these cameras'
    # HEVC, so even a silent camera needs an H.264 one. Audio only adds the AAC track.
    # HD is transcoded once from the base stream and preloaded; SD is a local derivative of HD.
    silent_main_raw = _raw(hd=True, has_audio=False)
    silent_sub_raw = _raw(hd=False, has_audio=False)
    main_raw = _raw(hd=True)
    sub_raw = _raw(hd=False)
    silent_id, audible_id = silent.camera_id, audible.camera_id
    assert streams[silent_id] == "rtsp://admin@10.0.0.1:554/onvif1"  # recording
    assert streams[f"{silent_id}_hd"] == (f"ffmpeg:{silent_id}#async#video=h264{silent_main_raw}")
    assert streams[f"{silent_id}_web"] == (f"ffmpeg:{silent_id}_hd#video=h264_sd{silent_sub_raw}")
    assert streams[f"{audible_id}_hd"] == (
        f"ffmpeg:{audible_id}#async#video=h264#audio=aac#audio=opus{main_raw}"
    )
    assert (
        streams[f"{audible_id}_web"] == f"ffmpeg:{audible_id}_hd"
        f"#video=h264_sd#audio=aac#audio=opus{sub_raw}"
    )


def test_go2rtc_variants_never_open_the_camera_substream():
    """Recording/live/SD share one camera RTSP producer even when `/onvif2` is advertised."""
    cam = _camera(
        "aa:bb:cc:00:00:03",
        stream_path="/onvif1",
        last_ip="10.0.0.3",
        username="admin",
        password="pw",
        capabilities={"has_audio": True, "stream_paths": ["/onvif1", "/onvif2"]},
    )
    streams = go2rtc.build_config(cameras=[cam])["streams"]
    sid = cam.camera_id
    assert streams[sid] == "rtsp://admin:pw@10.0.0.3:554/onvif1"  # recording
    assert f"{sid}_sub" not in streams
    assert "onvif2" not in repr(streams)
    assert streams[f"{sid}_hd"].startswith(f"ffmpeg:{sid}#async#video=h264")
    assert streams[f"{sid}_web"].startswith(f"ffmpeg:{sid}_hd#video=h264_sd")


def test_substream_url_none_when_camera_has_one_path():
    cam = _camera(
        "aa:bb:cc:00:00:04",
        stream_path="/onvif1",
        last_ip="10.0.0.4",
        capabilities={"stream_paths": ["/onvif1"]},
    )
    assert cam.substream_url is None


def test_segment_seconds_clamped_to_minimum(monkeypatch):
    from backend.app import config

    monkeypatch.setenv("SEGMENT_SECONDS", "10")
    config.get_settings.cache_clear()
    assert config.get_settings().segment_seconds == 60  # clamped up to the minimum
    monkeypatch.setenv("SEGMENT_SECONDS", "300")
    config.get_settings.cache_clear()
    assert config.get_settings().segment_seconds == 300  # honoured when >= 60


# --- re-keying a camera's recordings (docs/DECISIONS.md §23 follow-up) ---------------


def _seed_on_disk(mac_safe, day="2026-07-27"):
    """Seed one segment both on disk and in the index, the way the recorder writes it."""
    recorder.init_db()
    root = Path(config.get_settings().recordings_dir)
    seg = root / mac_safe / day / "00" / "20260727_000000.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"x" * 10)
    with connect() as c:
        c.execute("DELETE FROM recordings")
        c.execute(
            "INSERT INTO recordings (mac,path,started_at,day,hour,size_bytes,duration_s,indexed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (mac_safe, str(seg), f"{day}T00:00:00", day, 0, 10, 60, "x"),
        )
    return root, seg


def test_rekey_segments_updates_compatibility_mac_without_moving_history():
    root, seg = _seed_on_disk("aabbccddeeff")
    moved = recorder.rekey_segments("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:01")
    assert moved == 1
    assert seg.exists()  # archive paths are never rewritten
    assert not (root / "aabbccddee01").exists()
    # Cached clients may still filter by the corrected MAC; canonical clients use camera_id.
    assert recorder.query_segments(mac="aa:bb:cc:dd:ee:01")["total"] == 1
    assert recorder.query_segments(mac="aa:bb:cc:dd:ee:ff")["total"] == 0
    assert recorder.query_segments()["items"][0]["path"] == str(seg)


def test_rekey_segments_never_touches_an_existing_destination():
    root, seg = _seed_on_disk("aabbccddeeff")
    destination = root / "aabbccddee01"
    destination.mkdir(parents=True)
    marker = destination / "keep"
    marker.write_text("second camera")
    assert recorder.rekey_segments("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:01") == 1
    assert seg.exists() and marker.read_text() == "second camera"


def test_rekey_segments_same_mac_is_a_noop():
    _seed_on_disk("aabbccddeeff")
    assert recorder.rekey_segments("aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF") == 0


def test_reindexing_legacy_directory_preserves_owner_after_native_rekey():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:01")
    root, seg = _seed_on_disk("aabbccddeeff")
    assert recorder.rekey_segments("aa:bb:cc:dd:ee:ff", camera.mac) == 1

    rec = recorder.Recorder(segment_seconds=60)
    rec.root = root
    rec._index(list_all=True)

    item = recorder.query_segments()["items"][0]
    assert item["path"] == str(seg)
    assert item["camera_id"] == camera.camera_id
    assert item["mac"] == "aabbccddee01"


def test_go2rtc_hd_variant_exists_and_is_preloaded_for_every_camera():
    """A single shared H.264 producer is hot before browsers connect, for all cameras."""
    dual = _camera(
        "aa:bb:cc:00:00:05",
        stream_path="/onvif1",
        last_ip="10.0.0.5",
        capabilities={"has_audio": True, "stream_paths": ["/onvif1", "/onvif2"]},
    )
    single = _camera(
        "aa:bb:cc:00:00:06",
        stream_path="/onvif1",
        last_ip="10.0.0.6",
        capabilities={"has_audio": True, "stream_paths": ["/onvif1"]},
    )
    cfg = go2rtc.build_config(cameras=[dual, single])
    streams = cfg["streams"]
    assert (
        streams[f"{dual.camera_id}_hd"] == f"ffmpeg:{dual.camera_id}#async"
        f"#video=h264#audio=aac#audio=opus{_raw(hd=True)}"
    )
    assert f"{single.camera_id}_hd" in streams
    assert cfg["preload"] == {
        f"{dual.camera_id}_hd": "video&audio",
        f"{single.camera_id}_hd": "video&audio",
    }


def test_live_transcodes_use_the_final_codec_template_for_frame_rate_and_gop(monkeypatch):
    """The final H.264 template owns pacing/GOP, after per-stream raw bitrate arguments.

    This ordering matters: go2rtc used to append its built-in ``-g 50`` after our raw ``-g 24``,
    silently changing recovery from two to more than four seconds. The fps filter also stops
    output when decoding stops instead of manufacturing fresh timestamps for a frozen picture.
    """
    monkeypatch.setenv("LIVE_FPS", "12")
    config.get_settings.cache_clear()
    cam = _camera(
        "aa:bb:cc:00:00:07",
        stream_path="/onvif1",
        last_ip="10.0.0.7",
        capabilities={"has_audio": True, "stream_paths": ["/onvif1", "/onvif2"]},
    )
    cfg = go2rtc.build_config(cameras=[cam])
    streams = cfg["streams"]
    template = cfg["ffmpeg"]["h264"]
    assert "-vf fps=12" in template
    assert "-g:v 24" in template
    assert "-g 50" not in template
    sid = cam.camera_id
    assert "-r 12" not in streams[f"{sid}_web"]
    assert "-r 12" not in streams[f"{sid}_hd"]
    assert f"{sid}_live" not in streams
    assert f"ffmpeg:{sid}#async#video=h264" in streams[f"{sid}_hd"]
    assert "-af aresample=async=1:first_pts=0" in streams[f"{sid}_hd"]
    assert cfg["preload"][f"{sid}_hd"] == "video&audio"
    assert "nobuffer" not in cfg["ffmpeg"]["rtsp"]
    assert "low_delay" not in cfg["ffmpeg"]["rtsp"]
    assert cfg["ffmpeg"]["rtsp"].endswith("-rtsp_flags prefer_tcp -i {input}")
    assert "-vf fps=12,scale=640:-2" in cfg["ffmpeg"]["h264_sd"]
    assert cfg["ffmpeg"]["output"].endswith("{output}#starttimeout=45")
    # The recording feed is never re-encoded, so it must stay a bare RTSP URL.
    assert streams[sid] == "rtsp://admin@10.0.0.7:554/onvif1"
    config.get_settings.cache_clear()


def test_live_fps_is_clamped_to_a_sane_range(monkeypatch):
    monkeypatch.setenv("LIVE_FPS", "0")
    config.get_settings.cache_clear()
    assert config.get_settings().live_fps == 1
    monkeypatch.setenv("LIVE_FPS", "999")
    config.get_settings.cache_clear()
    assert config.get_settings().live_fps == 60
    config.get_settings.cache_clear()


def test_grid_hd_is_off_by_default():
    """The grid must default to the substream.

    Two cameras on the full-resolution feed measured `cpu.pressure full avg10 = 29.7` on the
    2-vCPU host — the same CPU starvation that made the players freeze. Turning this on is an
    explicit, host-dependent opt-in, never the default.
    """
    config.get_settings.cache_clear()
    assert config.get_settings().grid_hd_max_cameras == 0
