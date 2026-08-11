"""Unit tests for the live-view quality policy (backend/app/media/quality.py)."""
import re

import pytest

from backend.app.media import quality


def test_levels_and_default_are_consistent():
    assert quality.DEFAULT_LEVEL in quality.LEVELS
    assert quality.LEVELS == ("low", "medium", "high", "max")


@pytest.mark.parametrize("bad", [None, "", "ultra", "MAX", "hi", 5])
def test_normalize_level_falls_back_to_default(bad):
    assert quality.normalize_level(bad) == quality.DEFAULT_LEVEL


@pytest.mark.parametrize("level", quality.LEVELS)
def test_normalize_level_passes_known_levels_through(level):
    assert quality.normalize_level(level) == level


def test_main_bitrate_is_always_higher_than_substream_at_the_same_level():
    # The 1080p main feed earns more bits than the ~640x360 substream at every level.
    for level in quality.LEVELS:
        assert quality.target_kbps(level, hd=True) > quality.target_kbps(level, hd=False)


def test_bitrate_is_monotonic_across_levels():
    for hd in (True, False):
        kbps = [quality.target_kbps(lvl, hd=hd) for lvl in quality.LEVELS]
        assert kbps == sorted(kbps)
        assert len(set(kbps)) == len(kbps)  # strictly increasing, no ties


def test_max_is_the_sharpest_level():
    for hd in (True, False):
        best = max(quality.target_kbps(lvl, hd=hd) for lvl in quality.LEVELS)
        assert quality.target_kbps("max", hd=hd) == best


def test_encode_raw_args_shape_and_content():
    args = quality.encode_raw_args("max", 10, hd=True)
    assert args.startswith("#raw=")
    # the quality lever: an explicit, capped bitrate
    kbps = quality.target_kbps("max", hd=True)
    assert f"-b:v {kbps}k" in args
    assert f"-maxrate {kbps}k" in args
    assert f"-bufsize {kbps * 2}k" in args
    # Timing/GOP must not be here: go2rtc expands raw args before its codec preset and would
    # silently override them. They live in the final H.264 template instead.
    assert "-r " not in args
    assert "-g " not in args


def test_h264_template_owns_frame_pacing_and_gop():
    template = quality.h264_encoder_template(15)
    # Required by go2rtc's multimode mapper when AAC and Opus are emitted together.
    assert template.startswith("-c:v ")
    assert "-vf fps=15" in template
    assert "-g:v 30" in template
    assert "-keyint_min:v 30" in template
    assert "-sc_threshold:v 0" in template
    assert "-fps_mode passthrough" in template
    # never zero, even at 1 fps
    assert "-g:v 2" in quality.h264_encoder_template(1)


def test_sd_h264_template_combines_pacing_and_scaling_in_one_filter_graph():
    template = quality.h264_encoder_template(10, width=640)
    assert "-vf fps=10,scale=640:-2" in template
    assert template.count("-vf ") == 1


def test_encode_raw_args_is_a_single_raw_block():
    # go2rtc takes one #raw= block with space-separated args; we must not emit two.
    args = quality.encode_raw_args("medium", 12, hd=True)
    assert args.count("#raw=") == 1
    assert "#" not in args[len("#raw=") :]  # no stray directive markers after the block


def test_encode_raw_args_can_repair_a_regressing_live_audio_clock():
    args = quality.encode_raw_args("max", 10, hd=True, repair_audio_clock=True)
    assert "-af aresample=async=1:first_pts=0" in args
    assert args.count("#raw=") == 1


def test_video_directive_is_software_h264_by_default():
    assert quality.video_h264_directive("") == "#video=h264"
    assert quality.video_h264_directive(None) == "#video=h264"
    assert quality.video_h264_directive(None, codec="h264_sd") == "#video=h264_sd"


@pytest.mark.parametrize("hw", ["vaapi", "cuda", "v4l2m2m", "rkmpp"])
def test_video_directive_adds_hardware_when_known(hw):
    assert quality.video_h264_directive(hw) == f"#video=h264#hardware={hw}"


@pytest.mark.parametrize("bad", ["", None, "magic", "VAAPI ", "qsv?"])
def test_normalize_hwaccel_rejects_unknown(bad):
    # unknown/empty -> "" so build_config never emits a bogus #hardware= that would break go2rtc
    out = quality.normalize_hwaccel(bad)
    assert out == "" or out in quality.HWACCELS


def test_normalize_hwaccel_is_case_insensitive():
    assert quality.normalize_hwaccel("VAAPI") == "vaapi"
    assert quality.normalize_hwaccel(" Cuda ") == "cuda"


def test_invalid_level_uses_default_bitrate():
    default = quality.target_kbps(quality.DEFAULT_LEVEL, hd=True)
    assert quality.target_kbps("nonsense", hd=True) == default
    # and produces valid args rather than crashing
    assert re.match(r"#raw=-b:v \d+k", quality.encode_raw_args("nonsense", 10, hd=True))
