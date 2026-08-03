from pathlib import Path

from backend.app import config
from backend.app.api import routes
from backend.app.db import connect
from backend.app.media import go2rtc, quality
from backend.app.recording import recorder


def _raw(*, hd: bool) -> str:
    """The expected go2rtc ``#raw=`` chunk under the current settings — computed, not hardcoded,
    so these wiring tests stay correct if the quality bitrates are ever retuned."""
    s = config.get_settings()
    return quality.encode_raw_args(s.live_quality, s.live_fps, hd=hd)


def _seed(n, mac="aabbccddeeff", day="2026-07-27"):
    recorder.init_db()
    with connect() as c:
        c.execute("DELETE FROM recordings")
        c.executemany(
            "INSERT INTO recordings (mac,path,started_at,day,hour,size_bytes,duration_s,indexed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(mac, f"/r/{i}.mp4", f"{day}T00:{i:02d}:00", day, 0, 1000, 60, "x") for i in range(n)],
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


def test_recordings_endpoint_includes_retention_days(monkeypatch):
    _seed(3)
    monkeypatch.setenv("RECORDING_RETENTION_DAYS", "7")
    config.get_settings.cache_clear()
    res = routes.recordings()                       # route fn is callable directly (FastAPI)
    assert res["total"] == 3 and res["retention_days"] == 7    # page context for the UI banner
    monkeypatch.setenv("RECORDING_RETENTION_DAYS", "0")
    config.get_settings.cache_clear()
    assert routes.recordings()["retention_days"] == 0          # 0 = kept forever


def test_media_streams_reports_quality(monkeypatch):
    from types import SimpleNamespace
    # media_streams reads request.app.state.media; None -> not healthy, no hardware needed.
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=None)))
    monkeypatch.setenv("LIVE_QUALITY", "high")
    config.get_settings.cache_clear()
    out = routes.media_streams(req)
    assert out["live_quality"] == "high"
    assert out["quality_levels"] == list(quality.LEVELS)
    assert out["healthy"] is False
    assert "grid_hd_max_cameras" in out


def _add_body(**kw):
    return routes.CameraIn(mac="aa:bb:cc:dd:ee:ff", name="Cam", username="admin",
                           password="x", stream_path="/onvif1", **kw)


def _fake_request():
    from types import SimpleNamespace
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=None, rec=None)))


def test_add_camera_auto_probes_capabilities(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry
    from backend.app.discovery import active_scan
    registry.init_db()
    monkeypatch.setattr(routes.rtsp, "check_credentials", lambda *a, **k: "ok")
    ports_seen = {}
    class _Caps:
        def to_dict(self): return {"driver": "yoosee", "ptz": True, "has_audio": True}
    monkeypatch.setattr(active_scan, "enumerate_ports", lambda ip: ports_seen.setdefault("ip", ip) or [554, 5000])
    monkeypatch.setattr(drivers, "probe", lambda cam, ports: _Caps())
    out = routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert out["capabilities"]["ptz"] is True          # probed + stored during add
    assert ports_seen["ip"] == "192.168.1.50"          # probed the camera's IP


def test_add_camera_without_ip_skips_probe(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry
    registry.init_db()
    monkeypatch.setattr(drivers, "probe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed")))
    out = routes.upsert_camera(_add_body(last_ip=None), _fake_request())
    assert out["capabilities"] == {}                   # no IP → no credential check, no probe, still added


def test_add_camera_probe_failure_is_swallowed(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry
    from backend.app.discovery import active_scan
    registry.init_db()
    monkeypatch.setattr(routes.rtsp, "check_credentials", lambda *a, **k: "ok")
    monkeypatch.setattr(active_scan, "enumerate_ports", lambda ip: [554])
    monkeypatch.setattr(drivers, "probe", lambda cam, ports: (_ for _ in ()).throw(RuntimeError("boom")))
    out = routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"           # camera still added despite probe failure


def test_add_camera_rejects_wrong_credentials(monkeypatch):
    import pytest

    from backend.app.db import registry
    registry.init_db()
    monkeypatch.setattr(routes.rtsp, "check_credentials", lambda *a, **k: "auth")   # camera rejects creds
    with pytest.raises(routes.HTTPException) as ei:
        routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert ei.value.status_code == 422    # 422 (validation), NOT 401 — 401 would bounce the UI to login
    assert registry.get_camera("aa:bb:cc:dd:ee:ff") is None    # rejected → not saved


def test_add_camera_unreachable_is_allowed(monkeypatch):
    from backend.app import drivers
    from backend.app.db import registry
    from backend.app.discovery import active_scan
    registry.init_db()
    monkeypatch.setattr(routes.rtsp, "check_credentials", lambda *a, **k: "unreachable")  # offline, ambiguous
    monkeypatch.setattr(active_scan, "enumerate_ports", lambda ip: [554])
    monkeypatch.setattr(drivers, "probe", lambda cam, ports: (_ for _ in ()).throw(RuntimeError("offline")))
    out = routes.upsert_camera(_add_body(last_ip="192.168.1.50"), _fake_request())
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"           # unreachable ≠ wrong password → still added


def test_go2rtc_restart_managed_reexecs_binary(monkeypatch):
    g = go2rtc.Go2rtc(manage=True)
    calls = []
    monkeypatch.setattr(g, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(g, "start", lambda cams=None: calls.append("start"))
    monkeypatch.setattr(g, "reload_external", lambda: calls.append("reload"))
    g.restart()
    assert calls == ["stop", "start"]              # owns the binary → re-exec, no API reload


def test_go2rtc_restart_external_reloads_never_spawns(monkeypatch, tmp_path):
    from backend.app.db import registry
    registry.init_db()                             # write_config reads the (empty) cameras table
    g = go2rtc.Go2rtc(manage=False, config_path=tmp_path / "g.yaml")
    calls = []
    monkeypatch.setattr(g, "start", lambda cams=None: calls.append("start"))   # must NOT be called
    monkeypatch.setattr(g, "reload_external", lambda: calls.append("reload") or True)
    g.restart()
    assert calls == ["reload"]                     # external → config reload, never spawns a binary
    assert (tmp_path / "g.yaml").exists()          # config regenerated for the external go2rtc


def test_reload_external_posts_restart(monkeypatch):
    seen = {}
    class _R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url; seen["method"] = req.get_method()
        return _R()
    monkeypatch.setattr(go2rtc.urllib.request, "urlopen", fake_urlopen)
    assert go2rtc.Go2rtc(manage=False).reload_external() is True
    assert seen["url"].endswith("/api/restart") and seen["method"] == "POST"


def test_reload_external_false_on_transport_error(monkeypatch):
    monkeypatch.setattr(go2rtc.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(OSError("down")))
    assert go2rtc.Go2rtc(manage=False).reload_external() is False


def test_resync_is_best_effort_on_media_error():
    from types import SimpleNamespace
    class _Media:
        def restart(self): raise RuntimeError("boom")
        def wait_healthy(self, timeout): return False
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(media=_Media(), rec=None)))
    routes._resync(req)                            # must not raise — CRUD op stays successful


def test_go2rtc_config_binds_loopback():
    cfg = go2rtc.build_config(cameras=[])
    for section in ("api", "rtsp", "webrtc"):
        listen = cfg[section]["listen"]
        assert listen.startswith("127.0.0.1:")


def test_stream_id_and_restream_url():
    assert go2rtc.stream_id("aa:bb:cc:dd:ee:01") == "cam_aabbccddee01"
    assert go2rtc.web_stream_id("aa:bb:cc:dd:ee:01") == "cam_aabbccddee01_web"
    assert go2rtc.restream_rtsp_url("aa:bb:cc:dd:ee:01").endswith("/cam_aabbccddee01")


def test_go2rtc_web_variant_exists_for_every_camera_audio_only_changes_the_track():
    from backend.app.db.registry import Camera
    silent = Camera(mac="aa:bb:cc:00:00:01", stream_path="/onvif1", last_ip="10.0.0.1")
    audible = Camera(mac="aa:bb:cc:00:00:02", stream_path="/onvif1", last_ip="10.0.0.2",
                     capabilities={"has_audio": True})
    streams = go2rtc.build_config(cameras=[silent, audible])["streams"]
    # The live variant is about **video**, not audio: the browser cannot play these cameras'
    # HEVC, so even a silent camera needs an H.264 one. Audio only adds the AAC track.
    # (Neither camera advertises a substream here, so both re-encode the main feed -> `main_raw`.)
    main_raw = _raw(hd=True)
    assert streams["cam_aabbcc000001"] == "rtsp://admin@10.0.0.1:554/onvif1"      # recording
    assert streams["cam_aabbcc000001_web"] == f"ffmpeg:cam_aabbcc000001#video=h264{main_raw}"
    assert (streams["cam_aabbcc000002_web"]
            == f"ffmpeg:cam_aabbcc000002#video=h264#audio=aac#audio=opus{main_raw}")


def test_go2rtc_live_variant_prefers_the_substream():
    """Live view re-encodes the **substream** to H.264; the main feed is left for recording.

    The browser needs H.264 (handing it the cameras' HEVC yields a wrong fMP4 track header and
    jittering sample durations — the freezing). Re-encoding is therefore unavoidable here, so it
    is done on the cheap 640x360 feed (~5% of a core) instead of 1080p (~26%). The substream is
    pulled as its own go2rtc stream because the cameras reject ffmpeg's interleaved-TCP RTSP,
    and audio is merged from the main feed since the substream carries none.
    """
    from backend.app.db.registry import Camera
    cam = Camera(mac="aa:bb:cc:00:00:03", stream_path="/onvif1", last_ip="10.0.0.3",
                 username="admin", password="pw",
                 capabilities={"has_audio": True, "stream_paths": ["/onvif1", "/onvif2"]})
    streams = go2rtc.build_config(cameras=[cam])["streams"]
    assert streams["cam_aabbcc000003"] == "rtsp://admin:pw@10.0.0.3:554/onvif1"   # recording
    assert streams["cam_aabbcc000003_sub"] == "rtsp://admin:pw@10.0.0.3:554/onvif2"
    assert streams["cam_aabbcc000003_web"] == [
        # cheap re-encode off the substream -> substream bitrate (`hd=False`)
        f"ffmpeg:cam_aabbcc000003_sub#video=h264{_raw(hd=False)}",
        "ffmpeg:cam_aabbcc000003#audio=aac#audio=opus",  # audio only, off the main feed
    ]


def test_substream_url_none_when_camera_has_one_path():
    from backend.app.db.registry import Camera
    cam = Camera(mac="aa:bb:cc:00:00:04", stream_path="/onvif1", last_ip="10.0.0.4",
                 capabilities={"stream_paths": ["/onvif1"]})
    assert cam.substream_url is None


def test_segment_seconds_clamped_to_minimum(monkeypatch):
    from backend.app import config
    monkeypatch.setenv("SEGMENT_SECONDS", "10")
    config.get_settings.cache_clear()
    assert config.get_settings().segment_seconds == 60      # clamped up to the minimum
    monkeypatch.setenv("SEGMENT_SECONDS", "300")
    config.get_settings.cache_clear()
    assert config.get_settings().segment_seconds == 300     # honoured when >= 60


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


def test_rekey_segments_moves_files_and_index():
    root, seg = _seed_on_disk("aabbccddeeff")
    moved = recorder.rekey_segments("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:01")
    assert moved == 1
    assert not seg.exists()                                   # directory renamed
    new_seg = root / "aabbccddee01" / "2026-07-27" / "00" / "20260727_000000.mp4"
    assert new_seg.exists()
    # the recordings browser filters by MAC — the history must follow the camera
    assert recorder.query_segments(mac="aa:bb:cc:dd:ee:01")["total"] == 1
    assert recorder.query_segments(mac="aa:bb:cc:dd:ee:ff")["total"] == 0
    assert recorder.query_segments()["items"][0]["path"] == str(new_seg)


def test_rekey_segments_refuses_to_clobber_an_existing_destination():
    root, seg = _seed_on_disk("aabbccddeeff")
    (root / "aabbccddee01").mkdir(parents=True)                # destination already in use
    assert recorder.rekey_segments("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:01") == 0
    assert seg.exists()                                        # nothing moved, nothing lost
    assert recorder.query_segments(mac="aa:bb:cc:dd:ee:ff")["total"] == 1


def test_rekey_segments_same_mac_is_a_noop():
    _seed_on_disk("aabbccddeeff")
    assert recorder.rekey_segments("aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF") == 0


def test_go2rtc_hd_variant_reencodes_only_when_a_substream_exists():
    """The `_hd` variant is the deliberate exception: it *does* re-encode the video.

    Grid view runs on the cheap substream passthrough (`_web`); single view swaps to this one,
    which repairs the camera's malformed 1080p bitstream at ~27% of a core. go2rtc leaves the
    ffmpeg idle until a viewer actually consumes it, so grid view pays nothing for it. A camera
    with no substream has nothing to swap between and gets no `_hd`.
    """
    from backend.app.db.registry import Camera
    dual = Camera(mac="aa:bb:cc:00:00:05", stream_path="/onvif1", last_ip="10.0.0.5",
                  capabilities={"has_audio": True, "stream_paths": ["/onvif1", "/onvif2"]})
    single = Camera(mac="aa:bb:cc:00:00:06", stream_path="/onvif1", last_ip="10.0.0.6",
                    capabilities={"has_audio": True, "stream_paths": ["/onvif1"]})
    streams = go2rtc.build_config(cameras=[dual, single])["streams"]
    assert (streams["cam_aabbcc000005_hd"]
            == f"ffmpeg:cam_aabbcc000005#video=h264#audio=aac#audio=opus{_raw(hd=True)}")
    assert "cam_aabbcc000006_hd" not in streams


def test_live_transcodes_are_pinned_to_a_fixed_frame_rate(monkeypatch):
    """Every browser-facing transcode carries `-r <live_fps>`.

    Not cosmetic: the cameras send no PTS, so without a fixed rate ffmpeg reproduces their
    67/133 ms jitter and the player stalls (docs/DECISIONS.md §34). It also stops the encoder
    paying for duplicated frames — 20 -> 10 fps halved CPU for two 1080p streams at an
    identical bitrate.
    """
    from backend.app.db.registry import Camera
    monkeypatch.setenv("LIVE_FPS", "12")
    config.get_settings.cache_clear()
    cam = Camera(mac="aa:bb:cc:00:00:07", stream_path="/onvif1", last_ip="10.0.0.7",
                 capabilities={"has_audio": True, "stream_paths": ["/onvif1", "/onvif2"]})
    streams = go2rtc.build_config(cameras=[cam])["streams"]
    # The fixed rate lives inside the #raw= block (which now also carries quality bitrate args).
    assert "#raw=-r 12 " in streams["cam_aabbcc000007_web"][0]
    assert "#raw=-r 12 " in streams["cam_aabbcc000007_hd"]
    # The recording feed is never re-encoded, so it must stay a bare RTSP URL.
    assert streams["cam_aabbcc000007"] == "rtsp://admin@10.0.0.7:554/onvif1"
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
