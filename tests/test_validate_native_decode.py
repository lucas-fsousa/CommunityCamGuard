import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from scripts import validate_native_decode as module
from tests.test_captured_media import header


def sample(kind="video"):
    value = module.Sample("flow5", kind)
    value.consume("flow5", V1Record(encoding=unpack_v1_encoding_header(header())))
    value.consume("flow5", V1Record(video=b"video", audio=(b"audio",)))
    return value


def test_sample_ignores_other_flow_and_is_byte_bounded(monkeypatch):
    value = sample()
    value.consume("flow6", V1Record(video=b"private-other-camera"))
    assert value.data == b"video"
    monkeypatch.setattr(module, "MAX_BYTES", 5)
    with pytest.raises(ValueError, match="budget"):
        value.consume("flow5", V1Record(video=b"x"))
    assert value.data == b"video"


def test_no_header_or_configuration_change_is_rejected():
    value = module.Sample("flow5", "audio")
    with pytest.raises(ValueError, match="header"):
        value.consume("flow5", V1Record(audio=(b"x",)))
    value = sample()
    with pytest.raises(ValueError, match="configuration"):
        value.consume("flow5", V1Record(encoding=replace(value.encoding, video_width=640)))


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_sequential_decoders_limited_and_output_sanitized(monkeypatch, kind):
    calls = []
    metadata = (dict(codec_name="hevc", width=1920, height=1080) if kind == "video"
                else dict(codec_name="aac", sample_rate="16000", channels=1))
    metadata.update(nb_read_frames="1", unexpected="do-not-output")

    def run(command, **kwargs):
        calls.append(command)
        assert command[:5] == ["prlimit", "--as=536870912", "--cpu=30", "--nofile=64", "--"]
        assert command[command.index("-threads") + 1] == "1"
        assert command[command.index("-protocol_whitelist") + 1] == "pipe"
        assert kwargs["timeout"] == 45 and kwargs["stderr"] == subprocess.DEVNULL
        if command[5] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [metadata]}).encode())
        assert command[5] == "ffmpeg" and "-xerror" in command
        assert command[-3:] == ["-f", "null", "-"]
        assert kwargs["stdout"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    result = module.validate(sample(kind))
    assert len(calls) == 2
    assert result["strict_decode_ok"] and result["header_matches"] and result["frame_count_matches"]
    assert "unexpected" not in result["decoder"]


@pytest.mark.parametrize("failure", ["codec", "empty", "probe", "no_frames", "timeout"])
def test_failed_preconditions_or_probe_never_launch_decode(monkeypatch, failure):
    value = sample()
    calls = []
    if failure == "codec":
        value.encoding = replace(value.encoding, video_codec=99)
    elif failure == "empty":
        value.data.clear()

    def run(*args, **kwargs):
        calls.append(args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ffprobe", 45)
        return SimpleNamespace(returncode=1 if failure == "probe" else 0,
                               stdout=b'{"streams":[]}')

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        module.validate(value)
    assert len(calls) <= 1


def test_decode_errors_and_metadata_mismatch_are_not_success(monkeypatch):
    replies = iter([
        SimpleNamespace(returncode=0, stdout=b'{"streams":[{"codec_name":"hevc","width":640,"height":360,"nb_read_frames":"2"}]}'),
        SimpleNamespace(returncode=1),
    ])
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: next(replies))
    result = module.validate(sample())
    assert not result["strict_decode_ok"]
    assert not result["header_matches"]
    assert not result["frame_count_matches"]
