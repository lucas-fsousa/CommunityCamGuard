import json
import subprocess
from types import SimpleNamespace

import pytest

from backend.app.diagnostics import native_video_decode as decoder
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from tests.test_av_sample import sample
from tests.test_hevc_recovery import idr


@pytest.mark.parametrize("fault", [None, "probe", "decode", "timeout", "json", "dimensions",
                                  "frames", "codec", "oversize", "cancel"])
def test_sequential_decode_is_bounded_and_fail_closed(monkeypatch, fault):
    value = sample()
    value.consume(V1Record(video=idr(), video_timestamp=1))
    calls = []
    monkeypatch.setattr(decoder.shutil, "which", lambda name: "/usr/bin/" + name)

    def run(command, **kwargs):
        calls.append(command)
        assert command[:5] == ["prlimit", "--as=536870912", "--cpu=10", "--nofile=64", "--"]
        assert command[command.index("-threads") + 1] == "1"
        assert command[command.index("-protocol_whitelist") + 1] == "pipe"
        assert kwargs["timeout"] == 15 and kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["input"] is value.data
        if fault == "timeout":
            raise subprocess.TimeoutExpired("private", 15)
        if command[5] == "ffprobe":
            meta = dict(codec_name="hevc", width=1920, height=1080, nb_read_frames="1", private="secret")
            if fault == "dimensions":
                meta["width"] = 640
            if fault == "frames":
                meta["nb_read_frames"] = "2"
            if fault == "codec":
                meta["codec_name"] = "h264"
            output = json.dumps({"streams": [meta]}).encode()
            if fault == "json":
                output = b"private malformed output"
            if fault == "oversize":
                output = b" " * 4097
            return SimpleNamespace(returncode=int(fault == "probe"), stdout=output)
        assert command[5] == "ffmpeg" and "-xerror" in command
        assert command[-3:] == ["-f", "null", "-"]
        assert kwargs["stdout"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=int(fault == "decode"))

    monkeypatch.setattr(decoder.subprocess, "run", run)
    try:
        if fault:
            with pytest.raises(P2PProbeError, match="native video decode validation failed"):
                decoder.decode_sample(value, cancelled=lambda: fault == "cancel" and bool(calls))
            assert len(calls) == (2 if fault == "decode" else 1)
        else:
            result = decoder.decode_sample(value)
            assert len(calls) == 2 and result.frames == 1 and result.width == 1920
            assert "private" not in repr(result) and "secret" not in repr(result)
    finally:
        value.close()


def test_missing_tools_fail_before_subprocess(monkeypatch):
    monkeypatch.setattr(decoder.shutil, "which", lambda name: None)
    with pytest.raises(P2PProbeError, match="unavailable"):
        decoder.require_decoder_tools()


def test_empty_or_cancelled_sample_never_starts_child(monkeypatch):
    monkeypatch.setattr(decoder, "require_decoder_tools", lambda: None)
    monkeypatch.setattr(decoder.subprocess, "run", lambda *a, **k: pytest.fail("child started"))
    with pytest.raises(P2PProbeError):
        decoder.decode_sample(sample())
    value = sample()
    value.consume(V1Record(video=idr()))
    with pytest.raises(P2PProbeError):
        decoder.decode_sample(value, cancelled=lambda: True)
    value.close()
