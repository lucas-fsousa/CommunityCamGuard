"""Exact-unit operation proofs intersected with current property evidence.

Profiles must be supplied by trusted backend homologation records, never camera
metadata, HTTP input, enrollment or brand matching. No production profiles are
invented here. This selector is the migration target, not the legacy runtime gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..contracts import ControlDescriptor
from .capability_evidence import EvidenceState
from .capability_identity import CapabilityIdentity


@dataclass(frozen=True, slots=True)
class OperationProof:
    key: str
    readable: bool = False
    writable: bool = False
    options: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ValidatedProfile:
    camera_id: str
    identity: CapabilityIdentity
    operations: tuple[OperationProof, ...]


def select_controls(
    descriptors: tuple[ControlDescriptor, ...],
    *,
    camera_id: str,
    identity: CapabilityIdentity,
    profile: ValidatedProfile | None,
    evidence: Mapping[str, EvidenceState],
) -> tuple[ControlDescriptor, ...]:
    """Never promote read proof to write proof or property support to transport proof.

    Evidence must be resolved against this exact identity, time and rules by the
    backend store. Unknown/unsupported features, duplicate proofs and dynamic
    options without a dedicated validated enumeration flow are excluded.
    """
    if profile is None or profile.camera_id != camera_id or profile.identity != identity:
        return ()
    proofs = {proof.key: proof for proof in profile.operations}
    if len(proofs) != len(profile.operations):
        return ()
    selected = []
    for descriptor in descriptors:
        proof = proofs.get(descriptor.key)
        if (
            proof is None
            or evidence.get(descriptor.key) != EvidenceState.SUPPORTED
            or descriptor.dynamic_options
        ):
            continue
        readable = descriptor.readable and proof.readable is True
        writable = descriptor.writable and proof.writable is True
        options = tuple(option for option in descriptor.options if option in proof.options)
        if descriptor.options and not options:
            writable = False
        if readable or writable:
            selected.append(
                replace(descriptor, readable=readable, writable=writable, options=options)
            )
    return tuple(selected)
