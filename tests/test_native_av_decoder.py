"""One A/V decoder: no lost packets/continuity on backpressure, no pre-IDR audio."""

import pytest

from backend.app.media import native_av_decoder as module
from backend.app.media.decoder_command import decoder_command
from backend.app.media.fragmented_mp4 import FragmentedMp4


def test_one_decoder_owns_both_tracks_and_retains_backpressure(monkeypatch):
    calls = []

    class Decoder:
        pending_bytes = 0
        def __init__(self, **kwargs): calls.append(kwargs)
        def offer(self, data): calls.append(data); self.pending_bytes = len(data); return True
        def step(self): self.pending_bytes = 0
        def close(self): calls.append("closed")

    monkeypatch.setattr(module, "HevcDecoder", Decoder)
    output = FragmentedMp4()
    value = module.NativeAvDecoder(origin_us=1_000_000, output=output)
    assert calls == [{"output":output, "transport":"mpegts", "audio":True, "archive":None}]
    assert value.offer_audio(b"old", 999999)
    assert len(calls) == 1
    with pytest.raises(ValueError, match="PTS"):
        value.offer(b"video")
    assert value.offer(b"video", timestamp=1_000_000)
    assert not value.offer_audio(b"audio", 1_064_000)
    assert len(calls) == 2
    value.step()
    assert value.offer_audio(b"audio", 1_064_000)
    assert len(calls) == 3 and len(calls[1]) % 188 == len(calls[2]) % 188 == 0
    value.close()
    assert calls[-1] == "closed"


def test_aac_is_copied_and_native_pts_are_not_replaced_by_arrival_clock(tmp_path):
    command = decoder_command(output=True, live_timing=False, frame_rate=15,
        transport="mpegts", audio=True, archive=tmp_path / "%Y%m%d_%H%M%S.mp4")
    assert command.count("ffmpeg") == 1
    assert command[command.index("-c:a")+1] == "copy"
    assert "-use_wallclock_as_timestamps" not in command and "-r" not in command
    assert "+delay_moov" in command[command.index("-movflags")+1]
    assert command[command.index("-segment_time")+1] == "60"
    assert command[command.index("-c")+1] == "copy"
    with pytest.raises(ValueError):
        decoder_command(output=True, live_timing=False, frame_rate=None, audio=True)
