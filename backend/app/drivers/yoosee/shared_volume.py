"""Only the five reviewed volume positions enter the native shared writer."""

from ...db.p2p import P2PEnrollment
from ..contracts import ControlResult
from .p2p.volume import VOLUME_RAW_BY_PERCENT
from .shared_control_write import apply


def write(entry: P2PEnrollment, percent: int) -> ControlResult | None:
    if type(percent) is not int or percent not in VOLUME_RAW_BY_PERCENT:
        raise ValueError('speaker volume must be 0, 25, 50, 75 or 100 percent')
    return apply(entry, 'speaker_volume', percent, VOLUME_RAW_BY_PERCENT[percent],
                 lambda session: session.submit_volume_write(percent))
