"""Explicit finite legacy-V1 review input, never inferred from a transport ACK."""

from dataclasses import dataclass, field

from .audio_sender import validate_amr_audio_frames


@dataclass(frozen=True, slots=True)
class LegacyTalkInput:
    frames: tuple[bytes, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.frames, tuple) or any(not isinstance(f, bytes) for f in self.frames):
            raise ValueError('immutable legacy talk frames required')
        validate_amr_audio_frames(self.frames)
