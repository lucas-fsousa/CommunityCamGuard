"""Explicit startup cohort: reviewed members share one account and independent leases."""

from collections.abc import Mapping

from ...db.registry import Camera
from ...media.archive_spec import NativeArchiveSpec
from ...media.source_offer import NativeSourceOffer
from . import native_media_offer
from .native_account_service import NativeAccounts
from .native_cohort import NativeCohort
from .native_media_evidence import capture


def offers(cameras: tuple[Camera, ...], archives: Mapping[str, NativeArchiveSpec | None], *,
           accounts: NativeAccounts | None) -> tuple[NativeSourceOffer, ...]:
    if not cameras:
        return ()
    if len(cameras) == 1:
        offered = native_media_offer.offer(cameras[0], archive=archives.get(cameras[0].camera_id), accounts=accounts)
        return (offered,) if offered is not None else ()
    if accounts is None or len(cameras) > 3 or len({camera.camera_id for camera in cameras}) != len(cameras):
        return ()
    ordered = tuple(sorted(cameras, key=lambda camera: camera.camera_id))
    evidence = []
    for camera in ordered:
        archive = archives.get(camera.camera_id)
        if archive is not None and archive.camera_id != camera.camera_id:
            raise ValueError("archive must belong to the selected camera")
        item = capture(camera.camera_id, archive=archive is not None, now=lambda: native_media_offer.time())
        if item is None:
            return ()
        evidence.append(item)
    try:
        cohort = NativeCohort(tuple(evidence))
    except ValueError:
        return ()  # Separate accounts cannot share this SDK/account owner.
    return tuple(native_media_offer.build(camera, archive=archives.get(camera.camera_id), accounts=accounts,
                                         evidence=item, cohort=cohort)
                 for camera, item in zip(ordered, evidence, strict=True))
