"""Driver registry: discovery paths, detection, and per-family control gating."""

from types import SimpleNamespace

import pytest

from backend.app import drivers
from backend.app.db.registry import Camera
from backend.app.drivers.generic import GenericDriver

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


def test_identified_driver_gets_only_its_paths_with_camera_reported_path_first():
    paths = drivers.rtsp_paths_for(
        "dahua",
        channel=2,
        discovered=["/camera-reported", "/camera-reported"],
    )

    assert paths[0] == "/camera-reported"
    assert any(path.startswith("/cam/realmonitor") for path in paths)
    assert "/onvif1" not in paths
    assert "/Streaming/Channels/101" not in paths


def test_unknown_driver_retains_cross_family_fallback_paths():
    paths = drivers.rtsp_paths_for("generic")

    assert "/onvif1" in paths
    assert "/Streaming/Channels/101" in paths


# --- driver detection & selection --------------------------------------------------


def test_detect_yoosee_by_ports():
    ctx = drivers.DetectContext(open_ports=[554, 5000, 50000])
    assert drivers.detect(ctx).key == "yoosee"


def test_detect_dahua_by_vendor():
    assert drivers.detect(drivers.DetectContext(vendor="Dahua Technology")).key == "dahua"


def test_strong_vendor_identity_outranks_yoosee_port_fingerprint():
    context = drivers.DetectContext(
        vendor="Dahua Technology",
        model="IPC-HDW",
        open_ports=[554, 5000],
    )

    assert drivers.detect(context).key == "dahua"


def test_detect_falls_back_to_generic():
    assert drivers.detect(drivers.DetectContext(open_ports=[554])).key == "generic"


def test_for_camera_uses_stored_driver_key():
    cam = Camera(mac="aa:bb", capabilities={"driver": "yoosee"})
    assert drivers.for_camera(cam).key == "yoosee"
    assert drivers.for_camera(Camera(mac="cc:dd")).key == "generic"  # unknown -> generic


def test_for_camera_falls_back_to_detection_for_legacy_caps():
    # camera probed before drivers existed: no "driver" key, but ports are known
    legacy = Camera(mac="aa:bb", capabilities={"open_ports": [554, 5000, 50000]})
    assert drivers.for_camera(legacy).key == "yoosee"


def test_onboarding_is_resolved_through_the_selected_driver():
    provider = drivers.onboarding_provider("yoosee")

    assert provider is drivers.get("yoosee").onboarding()
    assert provider.driver_key == "yoosee"
    assert provider.provider == "yoosee-gwell"
    assert drivers.onboarding_providers() == (("yoosee", provider),)


def test_driver_without_onboarding_rejects_factory_enrollment():
    with pytest.raises(LookupError, match="does not support"):
        drivers.onboarding_provider("generic")


def test_registry_rejects_duplicate_keys_and_misplaced_generic_fallback():
    duplicate = (GenericDriver(), GenericDriver())
    with pytest.raises(RuntimeError, match="generic camera driver"):
        drivers._index_drivers(duplicate)

    first = GenericDriver()
    second = GenericDriver()
    first.key = "same"
    second.key = "same"
    fallback = GenericDriver()
    with pytest.raises(RuntimeError, match=r"duplicate camera driver key.*same"):
        drivers._index_drivers((first, second, fallback))


def test_registry_rejects_onboarding_owned_by_another_driver(monkeypatch):
    camera_driver = GenericDriver()
    camera_driver.key = "camera-family"
    camera_driver.onboarding = lambda: SimpleNamespace(
        driver_key="different-family",
        provider="Broken provider",
    )
    monkeypatch.setattr(drivers, "DRIVERS", (camera_driver, GenericDriver()))

    with pytest.raises(RuntimeError, match="expected 'camera-family'"):
        drivers.onboarding_providers()


# --- per-family control gating -----------------------------------------------------


def test_generic_driver_has_no_controls():
    cam = Camera(mac="aa:bb", last_ip="1.2.3.4")
    with pytest.raises(drivers.Unsupported):
        drivers.GENERIC.ptz(cam, "left")
    with pytest.raises(drivers.Unsupported):
        drivers.GENERIC.reboot(cam)


def test_yoosee_driver_does_not_advertise_family_wide_static_features():
    yoosee = drivers.get("yoosee")
    assert not hasattr(yoosee, "features")
    assert yoosee.control_catalog(Camera(mac="aa:bb", last_ip="1.2.3.4")) == ()
    # reboot stays Unsupported on Yoosee (Gwell P2P only)
    import pytest

    with pytest.raises(drivers.Unsupported):
        yoosee.reboot(Camera(mac="aa:bb", last_ip="1.2.3.4"))


def test_yoosee_ptz_bad_direction_raises_valueerror():
    import pytest

    with pytest.raises(ValueError):
        drivers.get("yoosee").ptz(Camera(mac="aa:bb", last_ip="1.2.3.4"), "zoom", "start")
