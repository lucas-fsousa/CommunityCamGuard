"""Only normal/180-degree orientation enters the native shared writer."""

from ...db.p2p import P2PEnrollment
from ..contracts import ControlResult
from .p2p.orientation import ORIENTATION_VALUES
from .shared_control_write import apply


def write(entry: P2PEnrollment, orientation: str) -> ControlResult | None:
    if type(orientation) is not str or orientation not in ORIENTATION_VALUES:
        raise ValueError('orientation must be normal or inverted')
    return apply(entry, 'orientation', orientation, ORIENTATION_VALUES[orientation],
                 lambda session: session.submit_orientation_write(orientation))
