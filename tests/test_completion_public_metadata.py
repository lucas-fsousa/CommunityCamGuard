"""Completion must not echo driver diagnostics or credential-bearing media URLs."""

from types import SimpleNamespace

from fastapi import Response

from backend.app.api import onboarding


def test_completion_omits_raw_stream_path_and_unknown_diagnostics(monkeypatch):
    secret = "SYNTHETIC_COMPLETION_SECRET"
    camera = SimpleNamespace(camera_id="cam_" + "a" * 24, name="Test", last_ip="192.0.2.3",
                             stream_path=f"rtsp://user:{secret}@192.0.2.3/live")
    completed = SimpleNamespace(camera=camera, already_configured=False,
        proof=SimpleNamespace(transport=secret, has_video=True, has_audio=True,
                              video_codec="h264", audio_codec=secret),
        stages=("identity", secret, "registry"))
    monkeypatch.setattr(onboarding, "inspect_provisioning_label", lambda body: {
        "device_id": "12345678", "mac": "aa:bb:cc:dd:ee:03", "firmware_version": "",
    })
    monkeypatch.setattr(onboarding, "onboarding", lambda: SimpleNamespace(complete=lambda **kwargs: completed))
    monkeypatch.setattr(onboarding, "resync_services", lambda request: None)
    result = onboarding.complete_onboarding(onboarding.CompleteOnboardingIn(), None, Response())
    assert secret not in str(result)
    assert "stream_path" not in result["camera"]
    assert result["media"]["video_codec"] == "h264"
    assert result["media"]["audio_codec"] == ""
    assert result["stages"] == ["identity", "registry"]
