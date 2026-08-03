"""Driver registry: discovery paths, detection, and per-family control gating."""
from backend.app import drivers
from backend.app.db.registry import Camera

# --- RTSP discovery paths (union across drivers) -----------------------------------

def test_yoosee_paths_come_first():
    paths = drivers.rtsp_paths()
    assert paths[0] == "/onvif1" and paths[1] == "/onvif2"


def test_paths_are_deduplicated():
    paths = drivers.rtsp_paths()
    assert len(paths) == len(set(paths))


def test_credential_templates_hidden_without_creds():
    assert not any("user=" in p for p in drivers.rtsp_paths())


def test_credential_templates_filled_with_creds():
    xm = [p for p in drivers.rtsp_paths("admin", "pw", channel=2) if p.startswith("/user=")]
    assert xm and "admin" in xm[0] and "pw" in xm[0] and "channel=2" in xm[0]


def test_channel_placeholder_filled():
    dahua = [p for p in drivers.rtsp_paths(channel=3) if p.startswith("/cam/realmonitor")]
    assert dahua and "channel=3" in dahua[0]


# --- driver detection & selection --------------------------------------------------

def test_detect_yoosee_by_ports():
    ctx = drivers.DetectContext(open_ports=[554, 5000, 50000])
    assert drivers.detect(ctx).key == "yoosee"


def test_detect_dahua_by_vendor():
    assert drivers.detect(drivers.DetectContext(vendor="Dahua Technology")).key == "dahua"


def test_detect_falls_back_to_generic():
    assert drivers.detect(drivers.DetectContext(open_ports=[554])).key == "generic"


def test_for_camera_uses_stored_driver_key():
    cam = Camera(mac="aa:bb", capabilities={"driver": "yoosee"})
    assert drivers.for_camera(cam).key == "yoosee"
    assert drivers.for_camera(Camera(mac="cc:dd")).key == "generic"   # unknown -> generic


def test_for_camera_falls_back_to_detection_for_legacy_caps():
    # camera probed before drivers existed: no "driver" key, but ports are known
    legacy = Camera(mac="aa:bb", capabilities={"open_ports": [554, 5000, 50000]})
    assert drivers.for_camera(legacy).key == "yoosee"


# --- per-family control gating -----------------------------------------------------

def test_generic_driver_has_no_controls():
    cam = Camera(mac="aa:bb", last_ip="1.2.3.4")
    import pytest
    with pytest.raises(drivers.Unsupported):
        drivers.GENERIC.ptz(cam, "left")
    with pytest.raises(drivers.Unsupported):
        drivers.GENERIC.reboot(cam)


def test_yoosee_driver_advertises_ptz_not_reboot():
    yoosee = drivers.get("yoosee")
    assert "ptz" in yoosee.features and "reboot" not in yoosee.features
    # reboot stays Unsupported on Yoosee (Gwell P2P only)
    import pytest
    with pytest.raises(drivers.Unsupported):
        yoosee.reboot(Camera(mac="aa:bb", last_ip="1.2.3.4"))


def test_yoosee_ptz_bad_direction_raises_valueerror():
    import pytest
    with pytest.raises(ValueError):
        drivers.get("yoosee").ptz(Camera(mac="aa:bb", last_ip="1.2.3.4"), "zoom", "start")
