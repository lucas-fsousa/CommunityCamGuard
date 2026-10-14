"""One identity/model decision for both legacy and shared-reader PTZ preparation."""

from .capability_identity import CapabilityIdentity, normalize_identity
from .p2p.contracts import P2PPropertyRead
from .ptz_models import directions_for


def identity_directions(product: P2PPropertyRead, version: P2PPropertyRead, *,
                        device_id: str, expected: CapabilityIdentity | None,
                        reviewed: frozenset[str]) -> frozenset[str]:
    """Return model-reviewed candidates; current axis evidence is STILL required.

    Inputs must come from correlated reads. An exact previously reviewed unit
    must match all identity fields; otherwise use the driver's model catalogue.
    No enrollment alone, frontend model claim or unknown firmware grants motion.
    """
    identity = normalize_identity(product, version, device_id=device_id)
    if identity is None or (expected is not None and identity != expected):
        raise ValueError("native PTZ identity does not match reviewed evidence")
    allowed = reviewed if expected is not None else reviewed & directions_for(identity)
    if not allowed:
        raise ValueError("native PTZ model profile is not supported")
    return allowed
