"""Bounded local FFmpeg roundtrip: two decoder epochs cannot overwrite one archive."""

import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from backend.app.media.archive_names import epoch_template, started_at
from backend.app.media.decoder_command import decoder_command


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("prlimit"), reason="local media tools required")
def test_original_av_survives_two_epochs_at_the_same_utc_second(tmp_path):
    fixture = subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-filter_threads", "1",
        "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1",
        "-c:v", "libx265", "-threads", "1", "-pix_fmt", "yuv420p",
        "-x265-params", "pools=none:frame-threads=1:log-level=error",
        "-c:a", "aac", "-b:a", "32k", "-f", "mpegts", "pipe:1",
    ], capture_output=True, timeout=15, check=True).stdout
    assert len(fixture) < 256 * 1024
    instant = datetime(2026, 10, 28, 23, 59, 59, tzinfo=UTC)
    paths, contents = [], []
    for _ in range(2):
        path = tmp_path / instant.strftime(epoch_template(tmp_path / "%Y%m%d_%H%M%S.mp4").name)
        command = decoder_command(output=True, live_timing=False, frame_rate=5,
                                  transport="mpegts", audio=True, archive=path)
        result = subprocess.run(command, input=fixture, capture_output=True, timeout=15, check=True)
        assert b"ftyp" in result.stdout[:32]
        assert path.is_file() and started_at(path) == instant
        if paths:
            assert paths[0].read_bytes() == contents[0]
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path),
        ], capture_output=True, timeout=10, check=True).stdout
        assert b"hevc" in probe and b"aac" in probe
        paths.append(path)
        contents.append(path.read_bytes())
    assert paths[0] != paths[1] and len(list(tmp_path.glob("*.mp4"))) == 2
