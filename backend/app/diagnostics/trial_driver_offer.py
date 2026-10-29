"""Explicit operator review in a private trial DB; never a public admission API."""

import hashlib
from pathlib import Path
from time import time

from .. import drivers
from ..db import p2p, registry
from ..drivers.yoosee import capability_profiles, capability_rollout, capability_snapshot_store
from ..drivers.yoosee.capability_collector import collect_snapshot
from ..drivers.yoosee.capability_policy import OperationProof, ValidatedProfile
from ..media.archive_spec import NativeArchiveSpec
from ..media.source_offer import NativeSourceOffer


def evidence_reference(path: Path) -> str:
    root = Path(__file__).resolve().parents[3]
    resolved = path.resolve()
    if (not resolved.is_relative_to(root / "docs") or resolved.suffix != ".md"
            or not resolved.is_file() or resolved.stat().st_size > 1024*1024):
        raise ValueError("bounded repository Markdown review evidence required")
    return f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()} {resolved.relative_to(root).as_posix()}#native-av"


def prepare_driver_offer(camera_id: str, enrollment: p2p.P2PEnrollment,
                         archive: NativeArchiveSpec, evidence: Path) -> NativeSourceOffer:
    """Operator attests the doc's A/V acceptance for this exact target, in a clone.

    Fresh correlated properties establish identity, not media support. Only the
    explicit host review supplies media proofs; no firmware/brand auto-promotion.
    No PTZ proof is registered, and collection sends only allowlisted reads.
    """
    reference = evidence_reference(evidence)
    camera = registry.get_camera_by_id(camera_id)
    if (camera is None or enrollment.camera_id != camera_id or archive.camera_id != camera_id
            or p2p.read_registered_enrollment(camera_id) != enrollment):
        raise ValueError("matching registered trial camera/enrollment/archive required")
    generation = capability_snapshot_store.begin(camera_id)
    snapshot = collect_snapshot(enrollment)
    if (snapshot is None or snapshot.camera_id != camera_id
            or snapshot.identity.device_id != enrollment.device_id):
        raise RuntimeError("fresh correlated trial identity unavailable")
    if not capability_snapshot_store.save(snapshot,generation=generation,expires_at=snapshot.collected_at+1800):
        raise RuntimeError("trial identity was superseded")
    proofs = (
        OperationProof("native_video",readable=True,options=frozenset({"hevc"})),
        OperationProof("native_av",readable=True,
                       options=frozenset({"hevc","aac_lc_16k_mono","original_archive"})),
    )
    profile = ValidatedProfile(camera_id,snapshot.identity,proofs)
    if not capability_profiles.register(profile,sources={proof.key:reference for proof in proofs},reviewed_at=time()):
        raise RuntimeError("newer trial review already exists")
    capability_rollout.activate(profile)
    offer = drivers.for_camera(camera).native_media_offer(camera,archive=archive)
    if offer is None or offer.valid() is not True:
        raise RuntimeError("driver declined the reviewed recording offer")
    return offer
