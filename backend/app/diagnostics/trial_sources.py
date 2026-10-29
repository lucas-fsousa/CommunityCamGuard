"""Source preparation/observation for the bounded host operator trial only."""

from dataclasses import dataclass, replace
from pathlib import Path

from ..db import p2p
from ..drivers.yoosee.native_recovery import wrap
from ..drivers.yoosee.native_video_source import NativeVideoSource
from ..media.recovering_source import RecoveringSource
from ..media.source_offer import NativeSourceOffer
from ..media.source_registry import ManagedSource
from ..recording.recorder import Recorder
from .native_audio_sample import NativeAudioSample
from .trial_driver_offer import prepare_driver_offer
from .trial_motion import motion_grants


@dataclass
class TrialSource:
    offer: NativeSourceOffer
    source: ManagedSource
    audio: NativeAudioSample | None = None

    @property
    def current(self) -> NativeVideoSource | None:
        active = self.source.current if isinstance(self.source,RecoveringSource) else self.source
        return active if isinstance(active,NativeVideoSource) else None

    @property
    def frame_rate(self) -> int | None:
        active = self.current
        return active.frame_rate if active is not None else None

    @property
    def restarts(self) -> int:
        return self.source.restarts if isinstance(self.source,RecoveringSource) else 0

    def diagnostics(self) -> dict[str,str | bool]:
        active = self.current
        return active.diagnostics() if active is not None else {"session_worker":"recovering"}

    def retire_session(self) -> bool:
        active = self.current
        if active is None: return False
        active._session.stop()
        return True


def prepare_source(camera_id: str, enrollment: p2p.P2PEnrollment, recorder: Recorder, *,
                   sample_audio: bool, native_av: bool, ptz_directions: frozenset[str],
                   recover: bool, driver_evidence: Path | None = None) -> TrialSource:
    if (recover and not native_av) or (sample_audio and native_av) or (ptz_directions and not native_av):
        raise ValueError("compatible trial audio/motion/recovery scope required")
    if driver_evidence is not None:
        if not native_av or sample_audio or ptz_directions:
            raise ValueError("reviewed driver archive trial is A/V-only, without motion")
        offer = prepare_driver_offer(camera_id,enrollment,recorder.native_archive(camera_id),driver_evidence)
        source = offer.factory()  # lazy driver construction only; no camera/decoder birth
        return TrialSource(replace(offer,factory=lambda: source),source)
    audio = NativeAudioSample() if sample_audio else None
    archive = Path(recorder._output_template(camera_id)) if native_av else None
    grants = motion_grants(enrollment,ptz_directions)
    def valid(): return p2p.read_registered_enrollment(camera_id) == enrollment
    def construct():
        return NativeVideoSource(camera_id,enrollment.device_id,delivery=audio,native_audio=native_av,
                                 archive=archive,motion_grants=grants,motion_valid=valid if grants else None)
    initial = construct()
    source = wrap(initial,camera_id=camera_id,factory=construct,valid=valid) if recover else initial
    offer = NativeSourceOffer(camera_id,NativeVideoSource.admission_domain,lambda: source,valid)
    return TrialSource(offer,source,audio)
