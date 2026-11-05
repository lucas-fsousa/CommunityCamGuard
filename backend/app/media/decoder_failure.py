"""Fixed decoder failure categories; raw stderr/paths are never returned or retained."""

_PATTERNS = (
    ('storage_full', (b'no space left on device', b'disk quota exceeded')),
    ('resource_limit', (b'cannot allocate memory', b'out of memory', b'resource temporarily unavailable')),
    ('output_path', (b'permission denied', b'no such file or directory', b'failed to open segment')),
    ('timestamp_order', (b'non monotonically increasing dts', b'non-monotonous dts', b'invalid dts')),
    ('audio_bitstream', (b'aac bitstream', b'error applying bitstream filters', b'error parsing adts')),
    ('input_headers', (b'non-existing pps', b'pps id out of range', b'vps id out of range', b'no start code is found')),
    ('input_bitstream', (b'invalid data found', b'error while decoding', b'invalid nal unit', b'could not find ref')),
    ('encoder_setup', (b'error while opening encoder', b'error initializing output stream', b'unknown encoder', b'unrecognized option')),
    ('output_write', (b'broken pipe', b'error writing trailer', b'error muxing a packet')),
)
FAILURE_CATEGORIES = frozenset(name for name, _ in _PATTERNS) | {'unknown'}


def classify(data: bytes) -> str:
    # Bounds also hold for calls made at child exit, not only normal progress lines.
    value = data[-16384:].lower()
    return next((name for name, patterns in _PATTERNS if any(pattern in value for pattern in patterns)), 'unknown')
