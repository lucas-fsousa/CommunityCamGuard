"""Public completion metadata, without raw media URLs or arbitrary stage text."""

from ..drivers.onboarding import CompletionResult

_STAGES = frozenset({"identity", "lan_discovery", "p2p_session", "rtsp_enabled",
                     "credential_delivered", "media_proof", "registry"})
_CODECS = frozenset({"h264", "hevc", "h265", "mjpeg", "mpeg4", "vp8", "vp9", "av1",
                     "aac", "opus", "pcm_alaw", "pcm_mulaw", "pcm_s16le", "mp3", "g726"})


def completion_metadata(completed: CompletionResult) -> dict[str, object]:
    """Unknown diagnostic vocabulary is omitted, not echoed or used for decisions."""
    proof = completed.proof
    # Driver-owned stages/codecs are diagnostics, not feature grants. Extend the
    # public vocabulary explicitly when introducing drivers with new terminology.
    def codec(value: object) -> str:
        return value if type(value) is str and value in _CODECS else ""

    return {
        "media": {
            "transport": proof.transport if proof.transport in ("udp", "tcp") else "",
            "has_video": proof.has_video is True,
            "has_audio": proof.has_audio is True,
            "video_codec": codec(proof.video_codec),
            "audio_codec": codec(proof.audio_codec),
        },
        "stages": [stage for stage in completed.stages
                   if type(stage) is str and stage in _STAGES],
    }
