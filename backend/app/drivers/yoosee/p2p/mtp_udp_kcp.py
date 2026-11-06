"""Normalize the observed direct-UDP KCP header variant, not relay envelopes."""

from .media_protocol import MTP_MAX_FRAME_SIZE, build_mtp_frame, verify_mtp_frame


def normalize_udp_kcp(frame: bytes) -> bytes:
    """Validate the ORIGINAL frame before normalizing its opaque low bit3.

    Camera3 returned c0/18 ACKs. Split SDK510bc51a UDP dispatch0x21a4cc-
    0x21a524 tests control bit7/data bit4, not low bit3. The KCP handler
    0x25f448-0x25f478 chooses the six-byte prefix for mode bits5/6==0.
    Accept only the two observed direct envelopes; no relay prefix stripping,
    ciphertext interpretation, conversation rewriting or checksum repair.
    """
    if (not 30 <= len(frame) <= MTP_MAX_FRAME_SIZE
            or frame[:2] not in (b"\xc0\x10", b"\xc0\x18")
            or not verify_mtp_frame(frame)):
        raise ValueError("invalid direct UDP KCP envelope")
    return frame if frame[1] == 0x10 else build_mtp_frame(0x10, frame[6:])
