"""Finite decoded evidence from the shared account's bounded video handoff."""

from collections.abc import Callable
from dataclasses import dataclass

from ..drivers.yoosee.p2p.account_read_worker import WorkerSummary
from ..drivers.yoosee.p2p.contracts import P2PProbeError
from ..drivers.yoosee.p2p.video_delivery import VideoDelivery
from .native_video_decode import DecodedVideo, decode_sample, require_decoder_tools
from .video_delivery_sample import VideoDeliverySample
from .yoosee_shared_media import run_reviewed_shared_media


@dataclass(frozen=True, slots=True)
class SharedDecodedResult:
    worker: WorkerSummary
    video: DecodedVideo


def run_reviewed_shared_decode(*, camera_id: str, device_id: str,
                               cancelled: Callable[[], bool] = lambda: False) -> SharedDecodedResult:
    require_decoder_tools()  # No authentication if decoder prerequisites are missing.
    delivery = VideoDelivery()
    consumer = VideoDeliverySample(delivery)
    try:
        consumer.start()
        result = run_reviewed_shared_media(camera_id=camera_id, device_id=device_id,
                                           cancelled=cancelled, video_delivery=delivery)
        delivery.close()
        consumer.join()
        if (consumer.failed or result.reason != "media_complete" or result.tcp is None
                or not result.tcp.close_acknowledged or result.media is None
                or not result.media.broker_release):
            raise P2PProbeError("shared video sample incomplete")
        # No native socket or consumer thread remains while bounded children decode.
        return SharedDecodedResult(result, decode_sample(consumer.sample, cancelled=cancelled))
    finally:
        delivery.close()
        consumer.close()
