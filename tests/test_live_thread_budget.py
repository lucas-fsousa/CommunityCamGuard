"""Input decoding, filter graphs and output encoding all receive the host budget."""

import pytest
from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.media import go2rtc


@pytest.mark.parametrize("threads", [1, 2, 8, 16])
def test_generated_templates_bound_both_sides_of_input(monkeypatch, threads):
    settings = get_settings().model_copy(update={"live_ffmpeg_threads": threads})
    monkeypatch.setattr(go2rtc, "get_settings", lambda: settings)
    templates = go2rtc.build_config(cameras=[])["ffmpeg"]
    before_input = templates["rtsp"].split("-i {input}")[0]
    assert f"-threads {threads} " in before_input
    assert f"-filter_threads {threads} " in before_input
    assert f"-filter_complex_threads {threads} " in before_input
    for name in ("h264", "h264_sd"):
        assert f"-threads:v {threads}" in templates[name]
        assert templates[name].startswith("-c:v ")


@pytest.mark.parametrize("threads", [0, -1, 17])
def test_settings_reject_unbounded_or_unreasonable_thread_counts(threads):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, live_ffmpeg_threads=threads)
