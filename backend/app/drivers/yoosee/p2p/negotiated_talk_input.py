"""Finite AAC preset recovered from LivePlayer's received-format callback."""

from dataclasses import dataclass, field

from .aac_lc import validate_aac_lc_adts_frame
from .stream_protocol import V1EncodingHeader

AAC_TALK_FORMAT = V1EncodingHeader(0x0102, 4, 2, 1, 16, 16000, 1024, 0, 0, 0, 0)


def matches_aac_talk(header: V1EncodingHeader | None) -> bool:
    return header is not None and all(getattr(header, name) == getattr(AAC_TALK_FORMAT, name)
        for name in ('audio_codec', 'audio_codec_option', 'audio_channels',
                     'audio_bit_width', 'audio_sample_rate', 'audio_frame_size'))


@dataclass(frozen=True, slots=True)
class NegotiatedAacTalkInput:
    frames: tuple[bytes, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.frames, tuple) or not 1 <= len(self.frames) <= 160
                or any(not isinstance(f, bytes) or not 7 <= len(f) <= 900 for f in self.frames)
                or sum(map(len, self.frames)) > 65536):
            raise ValueError('bounded immutable AAC frames required')
        for frame in self.frames:
            validate_aac_lc_adts_frame(frame)
