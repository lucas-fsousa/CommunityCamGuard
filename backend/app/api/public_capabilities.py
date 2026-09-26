"""Generic display metadata, separate from private driver evidence and controls."""

_FLAGS = frozenset({"reachable", "ptz", "reboot", "has_video", "has_audio"})
_LABELS = frozenset({"driver", "ptz_protocol", "model", "firmware", "video_codec",
                     "audio_codec", "probed_at"})


def public_capabilities(values: dict) -> dict[str, object]:
    """Driver internals and stream URLs must not implicitly become API fields."""
    result: dict[str, object] = {}
    for key in _FLAGS:
        if type(values.get(key)) is bool:
            result[key] = values[key]
    for key in _LABELS:
        value = values.get(key)
        if type(value) is str and len(value) <= 128:
            result[key] = value
    ports = values.get("open_ports")
    if isinstance(ports, list):
        result["open_ports"] = [port for port in ports[:128] if type(port) is int and 1 <= port <= 65535]
    # ports_by_role and stream_paths are internal discovery data, not UI capability
    # grants. A new public field requires an explicit contract, not a driver dump.
    return result
