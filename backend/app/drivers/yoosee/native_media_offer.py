"""Exact-unit reviewed native-video offers, never an enrollment/brand-only grant."""

from time import time

from ...db.registry import Camera
from ...media.archive_spec import NativeArchiveSpec
from ...media.source_offer import NativeSourceOffer
from ...media.source_registry import SourceUnavailable
from .account_video_source import AccountVideoSource
from .native_account_service import NativeAccounts
from .native_cohort import NativeCohort
from .native_lease_recovery import wrap as wrap_lease
from .native_media_evidence import NativeMediaEvidence, capture
from .native_recovery import wrap
from .native_video_source import NativeVideoSource


def offer(camera: Camera, *, archive: NativeArchiveSpec | None = None,
          accounts: NativeAccounts | None = None) -> NativeSourceOffer | None:
    camera_id = camera.camera_id
    if archive is not None and archive.camera_id != camera_id:
        raise ValueError("archive must belong to the selected camera")
    evidence = capture(camera_id, archive=archive is not None, now=lambda: time())
    if evidence is None:
        return None
    return build(camera, archive=archive, accounts=accounts, evidence=evidence)


def build(camera: Camera, *, archive: NativeArchiveSpec | None, accounts: NativeAccounts | None,
          evidence: NativeMediaEvidence, cohort: NativeCohort | None = None) -> NativeSourceOffer:
    camera_id, enrollment, captured = camera.camera_id, evidence.entry, evidence.grants
    valid = evidence.valid if cohort is None else cohort.current

    def construct():
        if not valid():
            raise SourceUnavailable("native media evidence changed")
        if archive is not None:
            return NativeVideoSource(camera_id, enrollment.device_id, native_audio=True,
                                     archive=archive.template, archive_segment_seconds=archive.segment_seconds,
                                     saving_allowed=archive.saving_allowed,
                                     motion_grants=captured, motion_valid=valid if captured else None)
        if captured:
            return NativeVideoSource(camera_id, enrollment.device_id, motion_grants=captured,
                                     motion_valid=valid)
        return NativeVideoSource(camera_id, enrollment.device_id)

    def factory():
        if accounts is not None:
            def bound(session):
                if not valid():
                    raise SourceUnavailable("native media evidence changed")
                if archive is not None:
                    return NativeVideoSource(camera_id, enrollment.device_id, native_audio=True,
                        archive=archive.template, archive_segment_seconds=archive.segment_seconds,
                        saving_allowed=archive.saving_allowed, shared_session=session)
                return NativeVideoSource(camera_id, enrollment.device_id, shared_session=session)
            def leased():
                return AccountVideoSource(enrollment, accounts, factory=bound, valid=valid,
                                          motion_grants=captured, cohort=cohort)
            return wrap_lease(leased(), camera_id=camera_id, factory=leased, valid=valid,
                              accounts=accounts, entry=enrollment)
        return wrap(construct(), camera_id=camera_id, factory=construct, valid=valid)

    if cohort is not None:
        return NativeSourceOffer(camera_id, "yoosee/media-lease/" + camera_id, factory, valid,
                                 restart_domains=frozenset({NativeVideoSource.admission_domain}))
    return NativeSourceOffer(camera_id, NativeVideoSource.admission_domain, factory, valid,
        restart_domains=frozenset({"yoosee/media-lease/" + camera_id}) if accounts is not None else frozenset())
