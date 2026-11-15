"""SDK received-format preset, exact live descriptor and 64ms AAC cadence."""

from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.negotiated_talk_input import (
    AAC_TALK_FORMAT,
    NegotiatedAacTalkInput,
    matches_aac_talk,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    decrypt_media_tlv,
    pack_v1_audio_encoding_header,
)
from tests.test_av_receive import COOKIE
from tests.test_av_shared_talk import active, sent
from tests.test_vendor_aac_lc import _adts


def test_negotiated_aac_emits_one_frame_per_64ms_and_preserves_shared_video():
    now = [0.0]
    session = active(now)
    talk = session.begin_talk(NegotiatedAacTalkInput((_adts(),) * 2))
    _, header = sent(session)
    assert decrypt_media_tlv(header.body, COOKIE) == pack_v1_audio_encoding_header(AAC_TALK_FORMAT)
    sent(session)  # Modern microphone ON, not legacy type5.
    _, audio = sent(session)
    assert decrypt_media_tlv(audio.body, COOKIE)[6:8] == b'\x01\x00'
    now[0] = .063
    assert not session.due()
    now[0] = .064
    sent(session)
    assert talk.acknowledged_frames == 2 and session.ready
    now[0] = .127
    assert not session.due()
    now[0] = .128
    sent(session)
    assert talk.done and talk.microphone_stopped and session.ready


def test_descriptor_mismatch_is_refused_before_any_sequence_or_microphone_command():
    session = active([0.0])
    session._receiver._encoding = replace(AAC_TALK_FORMAT, audio_sample_rate=8000)
    with pytest.raises(ValueError, match='descriptor'):
        session.begin_talk(NegotiatedAacTalkInput((_adts(),)))
    assert session.ready and not session.due()
    session.begin_finish()
    assert sent(session)[1].sequence == 1


@pytest.mark.parametrize('field,value', [('audio_codec', 5), ('audio_codec_option', 0),
    ('audio_channels', 2), ('audio_bit_width', 8), ('audio_sample_rate', 8000), ('audio_frame_size', 160)])
def test_every_audio_descriptor_field_must_match(field, value):
    assert not matches_aac_talk(replace(AAC_TALK_FORMAT, **{field: value}))
    assert matches_aac_talk(AAC_TALK_FORMAT) and not matches_aac_talk(None)


@pytest.mark.parametrize('frames', [(), [_adts()], (_adts(),) * 161, (b'bad',),
                                   (_adts(bytes(894)),), (_adts(bytes(500)),) * 160])
def test_input_rejects_mutability_truncation_and_frame_or_total_overflow(frames):
    with pytest.raises(ValueError):
        NegotiatedAacTalkInput(frames)
