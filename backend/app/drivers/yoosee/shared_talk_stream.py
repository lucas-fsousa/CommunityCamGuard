"""Bounded incremental canonical PCM -> AMR on the already owned native session."""

import time
from collections.abc import Iterable

from ...audio_format import MAX_PCM_BYTES, PCM_FRAME_BYTES, downsample_to_8khz
from ...db.p2p import P2PEnrollment
from ..contracts import AudioMessageResult, ControlNotReady, ControlOperationError
from .p2p.amr_nb import AmrNbEncoder
from .p2p.talk_stream_input import StreamingTalkInput
from .shared_control_reads import session_for
from .shared_talk import completed_result


def send_stream(entry: P2PEnrollment, chunks: Iterable[bytes]) -> AudioMessageResult:
    """Caller must have exact-unit STREAM proof; MESSAGE permission is insufficient.

    The producer may wait on its small input queue. AV consumption and microphone
    OFF remain on the existing account reader, never on this HTTP worker.
    """
    session = session_for(entry)
    if session is None:
        raise ControlNotReady("shared talk stream requires an existing native owner")
    source = StreamingTalkInput()
    # Initialize the local codec before submitting any microphone operation.
    try:
        encoder = AmrNbEncoder(max_seconds=10)
    except (OSError, RuntimeError, ValueError) as exc:
        source.close()
        raise ControlOperationError("native stream encoder unavailable") from exc
    ticket = None
    received = 0
    try:
        with encoder:
            if session_for(entry) is not session:
                raise ControlNotReady("native owner changed before stream submission")
            try:
                ticket = session.submit_talk(source)
            except ValueError:
                raise ControlNotReady("native owner is not accepting this stream") from None
            deadline = time.monotonic() + 18
            for chunk in chunks:
                if (not isinstance(chunk, bytes) or not chunk or len(chunk) % PCM_FRAME_BYTES
                        or received + len(chunk) > MAX_PCM_BYTES):
                    raise ValueError("bounded complete canonical PCM frames required")
                if source.aborted or time.monotonic() >= deadline:
                    raise ControlOperationError("native audio producer stopped or timed out; not repeated")
                if session_for(entry) is not session:
                    raise ControlOperationError("native owner changed during stream; not repeated")
                received += len(chunk)
                for frame in encoder.feed(downsample_to_8khz(chunk)):
                    source.put(frame)
            for frame in encoder.feed(b"", final=True):
                source.put(frame)
            source.finish()
        if received == 0:
            raise ControlOperationError("native stream received no audio")
        while time.monotonic() < deadline:
            result = session.take_talk(ticket)
            if result is not None:
                if source.aborted or session_for(entry) is not session:
                    raise ControlOperationError("native stream ended without confirmation; not repeated")
                return completed_result(received, result)
            if session_for(entry) is not session:
                raise ControlOperationError("native owner changed during stream drain; not repeated")
            time.sleep(.01)
        raise ControlOperationError("native stream drain timed out; not repeated")
    except (ControlNotReady, ControlOperationError):
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ControlOperationError("native stream failed; not repeated") from exc
    finally:
        if ticket is not None:
            session.abandon_talk(ticket)
        source.close()
