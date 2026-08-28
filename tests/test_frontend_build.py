"""Automatic, conflict-free application build identity and frontend bootstrap wiring."""
from __future__ import annotations

import json
from pathlib import Path

from backend.app import main
from backend.app.frontend_build import build_version


def test_build_version_is_deterministic_and_changes_with_runtime_code(tmp_path: Path):
    root = tmp_path / "project"
    backend = root / "backend" / "app"
    frontend = root / "frontend"
    backend.mkdir(parents=True)
    frontend.mkdir()
    (backend / "main.py").write_text("VALUE = 1\n")
    (frontend / "app.js").write_text("console.log(1);\n")
    (frontend / "ignored.txt").write_text("not executable\n")

    first = build_version(root, frontend)
    assert first == build_version(root, frontend)
    assert first.startswith("b-") and len(first) == 14

    (frontend / "app.js").write_text("console.log(2);\n")
    assert build_version(root, frontend) != first


def test_bootstrap_has_no_manually_incremented_asset_versions():
    frontend = Path(__file__).parents[1] / "frontend"
    index = (frontend / "index.html").read_text()
    player = (frontend / "player.js").read_text()
    boot = (frontend / "boot.js").read_text()

    assert '<script src="/boot.js"></script>' in index
    assert "2026-" not in index
    assert "2026-" not in player
    assert 'fetch("/api/build"' in boot
    assert "__CCG_BUILD__" in boot
    assert 'importMap.type = "importmap"' in boot
    assert '"ccg/core": moduleUrl("/modules/core.js")' in boot
    assert "await import(`/app.js?v=" in boot


def test_live_restart_cycles_producer_and_mse_discards_stale_video():
    """The operator's restart and the MSE fallback must both mean "back to live"."""
    frontend = Path(__file__).parents[1] / "frontend"
    live = (frontend / "modules" / "live-cameras.js").read_text()
    player = (frontend / "player.js").read_text()
    video_rtc = (frontend / "video-rtc.js").read_text()

    assert "refreshPlayer(cam.id, reload, true)" in live
    assert 'this.mode = "mse"' in player
    assert "const START_BUFFER_SECONDS = 3" in video_rtc
    assert "const CRITICAL_PLAYBACK_RATE = 0.85" in video_rtc
    assert "const LOW_PLAYBACK_RATE = 0.92" in video_rtc
    assert "const CATCHUP_PLAYBACK_RATE = 1.05" in video_rtc
    assert "void refreshPlayer(camera.id, null, true)" in live
    assert "event: 'live_edge_jump'" in video_rtc
    assert "this.video.currentTime = liveTarget" in video_rtc
    assert "Math.min(1.25, gap)" not in video_rtc


def test_vendor_controls_use_opaque_camera_id_and_explicit_target_states():
    live = (
        Path(__file__).parents[1] / "frontend" / "modules" / "live-cameras.js"
    ).read_text()

    assert "encodeURIComponent(cam.id)" in live
    assert "encodeURIComponent(cam.mac)}/${endpoint}" not in live
    assert "cam.controls || cam.vendor_controls" in live
    assert '"white-light", (value) => ({ enabled: value === "on" })' in live
    assert '"orientation", (orientation) => ({ orientation })' in live
    assert "Nothing is read automatically" in live


def test_camera_operations_address_api_by_opaque_id():
    live = (
        Path(__file__).parents[1] / "frontend" / "modules" / "live-cameras.js"
    ).read_text()

    for endpoint in ("ptz", "probe"):
        assert f"encodeURIComponent(cam.mac)}}/{endpoint}" not in live
    assert "`/cameras/${encodeURIComponent(cam.id)}/ptz`" in live
    assert "`/cameras/${encodeURIComponent(cam.id)}/probe`" in live
    assert '"/cameras/" + encodeURIComponent(cam.id)' in live
    assert "`/media/recover/${encodeURIComponent(cameraId)}`" in live
    assert "camera_id: cam.id" in live


def test_runtime_statuses_join_cameras_by_opaque_id():
    app = (Path(__file__).parents[1] / "frontend" / "app.js").read_text()

    assert "new Map(statuses.map((item) => [item.id, item]))" in app
    assert "byId.get(camera.id)" in app
    assert "byMac.get(camera.mac)" not in app


def test_frontend_entrypoint_only_orchestrates_semantic_modules():
    frontend = Path(__file__).parents[1] / "frontend"
    app = (frontend / "app.js").read_text()

    for specifier in ("ccg/core", "ccg/i18n", "ccg/live", "ccg/cameras", "ccg/recordings"):
        assert f'from "{specifier}"' in app
    assert len(app.splitlines()) < 200
    assert "function openProvisioningModal" not in app
    assert "function freezeWatchdog" not in app


def test_provisioning_modal_is_explicit_and_validates_identity_in_background():
    cameras = (Path(__file__).parents[1] / "frontend" / "modules" / "camera-management.js").read_text()

    assert 'close.addEventListener("click", dismiss)' in cameras
    assert 'overlay.addEventListener("click"' not in cameras
    assert 'document.addEventListener("keydown"' not in cameras
    assert 'label.addEventListener("input"' in cameras
    assert "scheduleIdentityInspection" in cameras
    assert "field.readOnly = locked" in cameras
    assert 'textContent: t("provision.inspect")' not in cameras
    assert 'password.addEventListener("input"' in cameras
    assert 'readiness.textContent = reason || t("provision.ready")' in cameras
    assert "response.manual_entry_allowed" in cameras
    assert 'api("/provisioning/networks/manual"' in cameras
    assert "browserCanProvision()" in cameras
    assert "state.provisioning?.remote_ble_enabled" in cameras
    assert "Boolean(state.provisioning?.transport_ready)" in cameras
    assert 'from "ccg/provisioning-ble"' in cameras
    assert "connectProvisioningCamera(deviceId.value, onBleNotification)" in cameras
    assert 'api("/provisioning/privileged/online-status"' in cameras
    assert "takeQueuedBleStage(prepared.expected_responses.wifi_connection)" in cameras
    assert "provision.bluetoothFinalResponseMissing" in cameras
    assert "connectionReply?.wifi_connection?.connected" in cameras
    assert 'api("/provisioning/privileged/bind"' in cameras
    assert 'api("/provisioning/privileged/complete"' in cameras
    assert 'api("/provisioning/vendor-account/login"' in cameras
    assert "state.provisioning.vendor_account_configured = true" in cameras
    assert 'textContent: t("provision.finishWifiOnly")' in cameras
    assert 'textContent: t("provision.bindPrivileged")' in cameras


def test_ble_provisioning_transport_uses_recovered_gatt_contract():
    frontend = Path(__file__).parents[1] / "frontend"
    ble = (frontend / "modules" / "camera-provisioning-ble.js").read_text()
    boot = (frontend / "boot.js").read_text()

    assert '"8922a5c3-1e44-403e-a587-bcf972e398b4"' in ble
    assert 'writeWithoutResponse: "0000fed7-0000-1000-8000-00805f9b34fb"' in ble
    assert "(count << 4) | index" in ble
    assert "navigator.bluetooth.requestDevice" in ble
    assert "navigator.bluetooth.getDevices" in ble
    assert "candidate.name === expectedName" in ble
    assert 'filters: [{ name: expectedName }]' in ble
    assert "export function createBleMessageAssembler()" in ble
    assert "export async function writeBleFrames(session, frames)" in ble
    assert "[notify, writeWithoutResponse, indicate]" in ble
    assert "firmware revisions return packets over FED7" in ble
    assert "response channel rejected" in ble
    assert "writeValueWithResponse" in ble
    assert "setTimeout(resolve, 35)" in ble
    assert 'api("/provisioning/ble/prepare"' in (frontend / "modules" / "camera-management.js").read_text()
    assert "prepared.frames.challenge, prepared.expected_responses.challenge" in (
        frontend / "modules" / "camera-management.js"
    ).read_text()
    cameras = (frontend / "modules" / "camera-management.js").read_text()
    # Factory onboarding stops at Wi-Fi. LAN discovery and privileged/RTSP onboarding have
    # separate user-visible stages instead of being hidden inside the BLE transaction.
    assert "waitForProvisionedCamera" not in cameras
    assert cameras.count('api("/discovery/scan"') == 1  # the main Scan Network action only
    assert "writeFrames(encodedFrames(finishFrames))" not in cameras
    assert 'api("/provisioning/ble/decode-response"' in cameras
    assert "if (challengeReply.valid !== true)" in cameras
    assert 'if (!wifiReply.json)' in cameras
    assert "configReply.binding" not in cameras
    assert "connectionReply?.wifi_connection?.connected" in cameras
    assert "bleFinishFrames = prepared.frames.finish || []" in cameras
    assert '"ccg/provisioning-ble": moduleUrl("/modules/camera-provisioning-ble.js")' in boot


def test_recording_rows_offer_download_without_triggering_playback_selection():
    frontend = Path(__file__).parents[1] / "frontend"
    recordings = (frontend / "modules" / "recordings.js").read_text()
    index = (frontend / "index.html").read_text()

    assert 'href: "/api/recordings/download?path="' in recordings
    assert 'event.stopPropagation()' in recordings
    assert 'svgIcon("i-download")' in recordings
    assert 'id="i-download"' in index
    assert "s.camera_name || nameOf[s.camera_id]" in recordings
    assert "camera_id: r.cameraId" in recordings
    assert "value: c.id" in recordings


def test_camera_status_indicator_uses_online_stream_liveness():
    frontend = Path(__file__).parents[1] / "frontend"
    app = (frontend / "app.js").read_text()
    live = (frontend / "modules" / "live-cameras.js").read_text()

    assert 'api("/cameras/status")' in app
    assert "setInterval(loadCameraStatuses, 5000)" in app
    assert 't(cam.online ? "cam.online" : "cam.offline")' in live
    assert 'className: "camera-status" +' in live


def test_recording_first_view_polls_until_seekable_cache_is_ready():
    recordings = (Path(__file__).parents[1] / "frontend" / "modules" / "recordings.js").read_text()
    assert "/recordings/prepare?path=" in recordings
    assert '{ method: "POST" }' in recordings
    assert "/recordings/playback-status?path=" in recordings
    assert "status.transcoding" in recordings
    assert "status.cached" in recordings
    assert "player.removeAttribute(\"src\")" in recordings
    assert "function playSeekable" in recordings
    assert 'player.src = fileUrl + "&ready=" + Date.now()' in recordings
    assert "player.src = fileUrl;" not in recordings


def test_build_endpoint_is_public_no_store_and_returns_content_id():
    response = main.frontend_build_info()
    body = json.loads(response.body)
    assert body["version"].startswith("b-")
    assert response.headers["cache-control"] == "no-store"
