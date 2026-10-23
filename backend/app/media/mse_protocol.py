"""Bounded MSE request grammar shared by native and restricted go2rtc delivery."""

import json

CODECS = frozenset({"avc1.640029", "avc1.64002A", "avc1.640033", "hvc1.1.6.L153.B0",
                    "mp4a.40.2", "mp4a.40.5", "flac", "opus"})


def mse_request(raw: object) -> str:
    """Validate the exact bounded request emitted by the bundled VideoRTC client."""
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate field")
            obj[key] = value
        return obj
    if not isinstance(raw, str) or len(raw) > 512:
        raise ValueError("Invalid MSE request")
    message = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(message, dict) or set(message) != {"type", "value"} or message["type"] != "mse":
        raise ValueError("MSE only")
    value = message["value"]
    if not isinstance(value, str):
        raise ValueError("Invalid codecs")
    codecs = value.split(",")
    if not codecs or len(codecs) != len(set(codecs)) or not set(codecs) <= CODECS:
        raise ValueError("Invalid codecs")
    return json.dumps(message, separators=(",", ":"))
