"""Bounded offline RTC mode-2 transform; intentionally not wired to reception."""

from .crypto import RC5
from .push_rtc import selective_rtc_cipher_span


def decrypt_selective_rtc(frame: bytes, *, key: bytes) -> bytes:
    """Decrypt full blocks only, preserving the clear prefix and short tail.

    Caller must establish SDK encryption mode 2 and key/session provenance.
    This is not device platform 2, authentication, or media validation. A wrong
    key is undetectable here. Empty/short spans remain unchanged, as in the SDK.
    No padding, network access, credential loading, or mode inference occurs.
    """
    if not isinstance(key, bytes) or len(key) != 8:
        raise ValueError("RTC key must contain exactly eight immutable bytes")
    offset, count = selective_rtc_cipher_span(frame)
    end = offset + (count // 8) * 8
    if end == offset:
        return frame
    cipher = RC5(key, rounds=6, w=32)
    result = bytearray(frame)
    for start in range(offset, end, 8):
        result[start:start + 8] = cipher.decrypt_block(frame[start:start + 8])
    return bytes(result)
