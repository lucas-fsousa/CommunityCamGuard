"""Server recording plans do not create files or depend on camera brands."""

from dataclasses import FrozenInstanceError

import pytest

from backend.app.camera_identity import stable_camera_id
from backend.app.media.archive_spec import NativeArchiveSpec
from backend.app.media.decoder_command import decoder_command
from backend.app.recording.recorder import Recorder

CAMERA = stable_camera_id("mac", "aa:bb:cc:dd:ee:03")


def test_recorder_supplies_its_actual_utc_path_and_segment_policy_without_io(tmp_path):
    recorder = Recorder(segment_seconds=300)
    spec = recorder.native_archive(CAMERA)
    assert spec.camera_id == CAMERA and spec.segment_seconds == 300
    assert str(spec.template) == recorder._output_template(CAMERA)
    assert not recorder.root.exists()
    assert spec.template.parent.name == "%H" and spec.template.parent.parent.name == "%Y-%m-%d"
    with pytest.raises(FrozenInstanceError): spec.segment_seconds = 60


@pytest.mark.parametrize("value", [True,False,59,0,-1,60.0,"300",2**31])
def test_invalid_policy_cannot_reach_a_decoder(tmp_path, value):
    with pytest.raises(ValueError,match="duration"):
        NativeArchiveSpec(CAMERA,tmp_path,value)
    with pytest.raises(ValueError,match="duration"):
        decoder_command(output=True,live_timing=False,frame_rate=15,archive_segment_seconds=value)


@pytest.mark.parametrize("camera", ["../outside","aa:bb:cc:dd:ee:03","cam_invalid",None])
def test_archive_owner_is_always_a_canonical_camera_id(tmp_path, camera):
    with pytest.raises(ValueError): NativeArchiveSpec(camera,tmp_path,300)


def test_archive_mux_policy_is_not_fixed_to_sixty_seconds(tmp_path):
    spec = NativeArchiveSpec(CAMERA,tmp_path,300)
    command = decoder_command(output=True,live_timing=False,frame_rate=15,transport="mpegts",
                              audio=True,archive=spec.template,archive_segment_seconds=spec.segment_seconds)
    assert command[command.index("-segment_time")+1] == "300"
    assert command.count("ffmpeg") == 1
    assert command[command.index("-c")+1] == "copy"  # original archive, no second encoder
