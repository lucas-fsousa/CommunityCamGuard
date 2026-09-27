"""Public numeric telemetry contract; arbitrary client strings never enter logs."""

import math

_NUMBERS = frozenset({
    "readyState", "networkState", "currentTime", "playbackRate", "bufferedStart", "bufferedEnd",
    "bufferedGap", "mseQueueBytes", "mseQueueLimit", "framesDecoded", "totalVideoFrames",
    "droppedVideoFrames", "mediaTime", "presentedFrames", "clientFrozenMs", "serverVideoPackets",
    "serverConsumers", "discardedSeconds", "bytesReceived", "packetsReceived", "packetsLost",
    "framesDropped", "jitter", "jitterBufferDelay", "jitterBufferEmittedCount", "freezeCount",
    "totalFreezesDuration", "frameWidth", "frameHeight", "framesPerSecond",
    "rtcPacketsReceived", "rtcPacketsLost", "rtcJitter", "rtcFramesReceived", "rtcFramesDropped",
    "rtcKeyFramesDecoded", "rtcFreezeCount", "rtcTotalFreezesDuration", "rtcJitterBufferDelay",
    "rtcJitterBufferEmittedCount",
})
_STATES = frozenset({"new", "checking", "connecting", "connected", "completed", "disconnected",
                     "failed", "closed"})


def public_metrics(metrics: dict) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in _NUMBERS:
        value = metrics.get(key)
        if (type(value) is int or type(value) is float) and abs(value) <= 1e15 and math.isfinite(value):
            result[key] = value
    for key in ("paused", "producerFrozen"):
        if type(metrics.get(key)) is bool:
            result[key] = metrics[key]
    transport = metrics.get("transport")
    if type(transport) is str and transport in {"mse", "webrtc", "connecting"}:
        result["transport"] = transport
    for key in ("connectionState", "iceConnectionState"):
        value = metrics.get(key)
        if type(value) is str and value in _STATES:
            result[key] = value
    if "error" in metrics:
        result["error_present"] = True
    return result
