"""Only the reviewed three-state scalar enters the existing native writer."""

from ...db.p2p import P2PEnrollment
from ..contracts import ControlResult
from .p2p.night_vision import NIGHT_VISION_VALUES
from .shared_control_write import apply


def write(entry: P2PEnrollment, mode: str) -> ControlResult | None:
    if type(mode) is not str or mode not in NIGHT_VISION_VALUES:
        raise ValueError('night-vision mode must be automatic, daytime or night')
    return apply(entry, 'night_vision', mode, NIGHT_VISION_VALUES[mode],
                 lambda session: session.submit_night_vision_write(mode))
