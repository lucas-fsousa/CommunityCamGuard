"""Finite PCM adapter to the existing native account; no fallback or new login."""

import time

from ...audio_format import MAX_PCM_BYTES, PCM_FRAME_BYTES, PCM_FRAME_MS, downsample_to_8khz
from ...db.p2p import P2PEnrollment
from ..contracts import AudioMessageResult, ControlNotReady, ControlOperationError
from .p2p.amr_nb import encode_pcm16le
from .p2p.legacy_talk_input import LegacyTalkInput
from .p2p.talk_inbox import TalkOutcome
from .shared_control_reads import session_for


def send_candidate(entry: P2PEnrollment, pcm: bytes, *, legacy: bool = False) -> TalkOutcome:
    """Low-level submission; caller owns diagnostic permission or exact route proof."""
    if not isinstance(pcm, bytes) or not PCM_FRAME_BYTES <= len(pcm) <= MAX_PCM_BYTES or len(pcm) % PCM_FRAME_BYTES:
        raise ValueError("complete bounded canonical PCM frames required")
    session = session_for(entry)
    if session is None:
        raise ControlNotReady("shared native talk requires an existing media owner")
    frames = encode_pcm16le(downsample_to_8khz(pcm))
    if session_for(entry) is not session:
        raise ControlNotReady("native owner changed before audio submission")
    try:
        ticket = session.submit_talk(LegacyTalkInput(frames) if legacy else frames)
    except ValueError:
        raise ControlNotReady("native owner is not accepting this utterance") from None
    try:
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            result = session.take_talk(ticket)
            if result is not None:
                if result.status == "transport_received" and session_for(entry) is not session:
                    raise ControlOperationError("native owner changed after audio; not repeated")
                return result
            if session_for(entry) is not session:
                raise ControlOperationError("native owner changed during audio; not repeated")
            time.sleep(.01)
        raise ControlOperationError("native talk timed out; not repeated")
    finally:
        session.abandon_talk(ticket)


def send_message(entry: P2PEnrollment, pcm: bytes) -> AudioMessageResult:
    """Selected only by explicit per-unit shared-message proof, not legacy AMR proof."""
    try:
        outcome = send_candidate(entry, pcm)
    except (ControlNotReady, ControlOperationError):
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ControlOperationError("shared audio transport failed; not repeated") from exc
    return completed_result(len(pcm), outcome)


def completed_result(pcm_bytes: int, outcome: TalkOutcome) -> AudioMessageResult:
    frames = pcm_bytes // PCM_FRAME_BYTES
    if not PCM_FRAME_BYTES <= pcm_bytes <= MAX_PCM_BYTES or pcm_bytes % PCM_FRAME_BYTES:
        raise ControlOperationError("shared audio duration is invalid")
    if (outcome.status != "transport_received" or outcome.acknowledged_frames != frames
            or not outcome.stop_acknowledged):
        raise ControlOperationError("shared audio transmission unconfirmed; not repeated")
    return AudioMessageResult(frames * PCM_FRAME_MS, frames, frames, frames,
        direct_connection=False, session_completed=True, route_released=False,
        shared_session=True, microphone_stop_acknowledged=True)
