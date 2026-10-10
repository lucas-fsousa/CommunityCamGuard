"""SDK 6.45 preconnection quality cache, not a platform claim or live command."""

from .video_definition import encode_definitions


def with_preconnect_definition(user_data: bytes, definition: int) -> bytes:
    """Reproduce LivePlayer's unknown-platform uniform preconnection cache.

    SDK 6.45 set_definitions stores BOTH fields when platform is neither 1 nor
    2, without sending BuiltIn command 5/0x33. set_opt_conn_params copies them
    into the 32-byte startup userdata. Only fresh experimental A4/INIT owners
    may use this template; it is not a mid-stream setter or an HD capability.
    All five packed slots match the SDK uniform setter; sparse maps are excluded.
    """
    if not isinstance(user_data, bytes) or len(user_data) != 32:
        raise ValueError("preconnection definition requires immutable 32-byte userdata")
    definitions = dict.fromkeys(range(5), definition)
    legacy = encode_definitions(1, definitions).payload
    packed = encode_definitions(2, definitions).payload
    result = bytearray(user_data)
    result[0:1] = legacy
    result[23:25] = packed
    return bytes(result)
