"""Private driver evidence is not the public capabilities/UI contract."""

from backend.app.api.camera_presenter import camera_out
from backend.app.api.public_capabilities import public_capabilities
from backend.app.db.registry import Camera


def test_display_metadata_omits_private_and_unbounded_fields():
    secret = "SYNTHETIC_CAMERA_SECRET"
    source = {"driver": "generic", "ptz": True, "has_audio": True, "video_codec": "h264",
              "stream_paths": [f"/live?token={secret}"], "accessToken": secret,
              "ports_by_role": {secret: [554]}, "private_evidence": {"token": secret},
              "open_ports": [554, True, -1, 65536, secret], "model": "M" * 129}
    result = public_capabilities(source)
    assert result == {"driver": "generic", "ptz": True, "has_audio": True,
                      "video_codec": "h264", "open_ports": [554]}
    assert source["private_evidence"]["token"] == secret


def test_unknown_or_invalid_flags_do_not_grant_features():
    assert public_capabilities({"ptz": "true", "has_audio": 1, "future_control": True}) == {}


def test_camera_response_omits_paths_but_preserves_driver_control_contract(monkeypatch):
    from backend.app.api import camera_presenter
    secret = "SYNTHETIC_PATH_SECRET"
    camera = Camera(camera_id="cam_" + "a" * 24, capabilities={"ptz": True, "has_audio": True,
        "private_evidence": secret}, stream_path=f"/live?token={secret}")
    observed = []

    def catalogue(value):
        observed.append(value.capabilities["private_evidence"])
        return {"orientation": {"supported": True}}

    monkeypatch.setattr(camera_presenter, "control_catalog", catalogue)
    result = camera_out(camera)
    assert secret not in str(result)
    assert "stream_path" not in result
    assert result["controls"] == {"orientation": {"supported": True}}
    assert observed == [secret]
    assert result["capabilities"]["ptz"] is True
