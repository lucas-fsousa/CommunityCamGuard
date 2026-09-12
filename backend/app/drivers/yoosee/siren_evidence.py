"""Exact Action.expelCtrl evidence; state support is not pulse/transport certification."""
from .capability_evidence import EvidenceState


def siren_state(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    timestamp = value.get("t")
    state = value.get("stVal")
    if (type(timestamp) is not int or not 0 < timestamp <= 0x7FFFFFFF
            or type(state) is not int or state not in (1, 2)):
        return None
    return state


def siren_evidence(value: object) -> EvidenceState:
    if isinstance(value, dict) and type(value.get("t")) is int and value["t"] == -1:
        return EvidenceState.UNSUPPORTED
    return EvidenceState.SUPPORTED if siren_state(value) is not None else EvidenceState.UNKNOWN
