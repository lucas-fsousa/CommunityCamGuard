"""Finite PCM adapter to the existing native account; no fallback or new login."""

import time

from ...audio_format import MAX_PCM_BYTES, PCM_FRAME_BYTES, downsample_to_8khz
from ...db.p2p import P2PEnrollment
from ..contracts import ControlNotReady, ControlOperationError
from .p2p.amr_nb import encode_pcm16le
from .p2p.talk_inbox import TalkOutcome
from .shared_control_reads import session_for


def send_candidate(entry: P2PEnrollment, pcm: bytes) -> TalkOutcome:
    """Internal physical-review operation, NOT a capability or dashboard selection."""
    if not isinstance(pcm, bytes) or not PCM_FRAME_BYTES <= len(pcm) <= MAX_PCM_BYTES or len(pcm) % PCM_FRAME_BYTES:
        raise ValueError("complete bounded canonical PCM frames required")
    session = session_for(entry)
    if session is None:
        raise ControlNotReady("shared native talk requires an existing media owner")
    frames = encode_pcm16le(downsample_to_8khz(pcm))
    if session_for(entry) is not session:
        raise ControlNotReady("native owner changed before audio submission")
    try:
        ticket = session.submit_talk(frames)
    except ValueError:
        raise ControlNotReady("native owner is not accepting this utterance") from None
    try:
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            result = session.take_talk(ticket)
            if result is not None:
                return result
            if session_for(entry) is not session:
                raise ControlOperationError("native owner changed during audio; not repeated")
            time.sleep(.01)
        raise ControlOperationError("native talk timed out; not repeated")
    finally:
        session.cancel_talk(ticket)
