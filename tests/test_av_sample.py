from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p import av_sample
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from tests.test_captured_media import header
from tests.test_hevc_recovery import idr, nal


def sample():
    value = av_sample.AvVideoSample()
    value.consume(V1Record(encoding=unpack_v1_encoding_header(header())))
    return value


def test_waits_for_configured_idr_and_never_retains_audio():
    value = sample()
    value.consume(V1Record(video=nal(1), video_timestamp=1, audio=(b"private-audio",)))
    assert value.discarded == 1 and not value.data
    value.consume(V1Record(video=idr(), video_timestamp=2))
    value.consume(V1Record(video=nal(1), video_timestamp=4))
    assert value.frames == 2 and value.data == idr() + nal(1)
    assert value.timestamp_span_ticks == 2
    assert "private" not in repr(value)
    value.close()
    assert value.closed and not value.data and value.encoding is None and value.frames == 0
    assert value.timestamp_span_ticks == 0
    value.close()
    with pytest.raises(ReceiveError):
        value.consume(V1Record(video=idr()))


@pytest.mark.parametrize("fault", ["regression", "corrupt", "epoch", "configuration", "budget"])
def test_failed_sample_clears_partial_payload(monkeypatch, fault):
    value = sample()
    value.consume(V1Record(video=idr(), video_timestamp=100))
    record = V1Record(video=nal(1), video_timestamp=101)
    if fault == "regression":
        record = replace(record, video_timestamp=99)
    elif fault == "corrupt":
        record = replace(record, video=b"private garbage")
    elif fault == "epoch":
        record = replace(record, video=idr(b"\x81"))
    elif fault == "configuration":
        record = V1Record(encoding=replace(value.encoding, video_width=640))
    else:
        monkeypatch.setattr(av_sample, "MAX_BYTES", len(value.data))
    with pytest.raises(ReceiveError, match="native video sample rejected"):
        value.consume(record)
    assert value.closed and not value.data and value.encoding is None


@pytest.mark.parametrize("field,number", [("video_codec", 99), ("video_width", 1921),
                                         ("video_height", 1081), ("video_width", 0)])
def test_unsupported_header_rejected(field, number):
    value = av_sample.AvVideoSample()
    encoding = replace(unpack_v1_encoding_header(header()), **{field: number})
    with pytest.raises(ReceiveError):
        value.consume(V1Record(encoding=encoding))
    assert value.closed and not value.data


def test_frame_cap_and_missing_header():
    value = sample()
    value.consume(V1Record(video=idr(), video_timestamp=1))
    for tick in range(2, 150):
        value.consume(V1Record(video=nal(1), video_timestamp=tick))
    assert value.frames == 120 and value.timestamp_span_ticks == 119
    missing = av_sample.AvVideoSample()
    with pytest.raises(ReceiveError):
        missing.consume(V1Record(video=idr()))
    assert missing.closed


def test_probe_sample_is_opt_in_and_owned_by_caller_on_success(monkeypatch):
    from tests import test_av_probe as probe
    from tests.test_v1_receive import av

    monkeypatch.setattr(probe, "av", lambda: av(video=idr()))
    value = av_sample.AvVideoSample()
    result = probe.run(probe.FakeSocket(), sample=value, duration=0.5)
    assert result.ready and result.close_acknowledged
    assert value.frames == 1 and not value.closed
    value.close()


@pytest.mark.parametrize("fault", ["silence", "invalid_media", "reused", "cancelled"])
def test_probe_failure_clears_sample_and_closes_socket(fault):
    from tests import test_av_probe as probe

    value = sample() if fault == "reused" else av_sample.AvVideoSample()
    sock = probe.FakeSocket("silence" if fault == "silence" else "normal")
    with pytest.raises((ReceiveError, ValueError)):
        probe.run(sock, sample=value, cancelled=lambda: fault == "cancelled")
    assert value.closed and not value.data and sock.closed
