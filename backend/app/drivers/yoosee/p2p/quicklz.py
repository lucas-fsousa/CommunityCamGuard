"""Narrow, memory-safe QuickLZ decoder for the Yoosee resource-service profile.

Only QuickLZ 1.5 level 2 blocks are accepted.  Compression and other levels deliberately remain
unsupported: this module exists solely to expand authenticated resource-service responses.
"""

from __future__ import annotations

import struct

_HASH_VALUES = 2048
_HASH_SLOTS = 4
_MAX_OUTPUT = 0x7FFF


def _block_sizes(source: bytes) -> tuple[int, int, int]:
    if len(source) < 3:
        raise ValueError("QuickLZ block is truncated")
    header_length = 9 if source[0] & 2 else 3
    if len(source) < header_length:
        raise ValueError("QuickLZ header is truncated")
    if header_length == 9:
        compressed_length, output_length = struct.unpack_from("<II", source, 1)
    else:
        compressed_length, output_length = source[1], source[2]
    if compressed_length != len(source):
        raise ValueError("QuickLZ compressed length does not match the block")
    if not 0 < output_length <= _MAX_OUTPUT:
        raise ValueError("QuickLZ output length is outside the resource-service limit")
    return header_length, compressed_length, output_length


def _hash_at(output: bytearray, position: int) -> int:
    value = output[position] | output[position + 1] << 8 | output[position + 2] << 16
    return ((value >> 9) ^ (value >> 13) ^ value) & (_HASH_VALUES - 1)


def decompress_level2(source: bytes, expected_length: int) -> bytes:
    """Expand one independent QuickLZ 1.5 level-2 block with strict bounds checking."""

    if not isinstance(source, bytes) or type(expected_length) is not int:
        raise TypeError("QuickLZ decoder requires bytes and an integer output length")
    header_length, compressed_length, output_length = _block_sizes(source)
    if expected_length != output_length:
        raise ValueError("QuickLZ header length does not match the gute frame")
    flags = source[0]
    if (flags >> 2) & 3 != 2:
        raise ValueError("QuickLZ block is not compression level 2")
    if not flags & 1:
        payload = source[header_length:]
        if len(payload) != output_length:
            raise ValueError("uncompressed QuickLZ payload length is invalid")
        return payload

    cursor = header_length
    control = 1
    output = bytearray()
    hash_positions: list[list[int | None]] = [
        [None] * _HASH_SLOTS for _index in range(_HASH_VALUES)
    ]
    hash_counts = bytearray(_HASH_VALUES)
    last_hashed = -1

    def update_hashes(through: int) -> None:
        nonlocal last_hashed
        if through >= len(output) - 2:
            raise ValueError("QuickLZ hash update exceeds available output")
        while last_hashed < through:
            last_hashed += 1
            hashed = _hash_at(output, last_hashed)
            count = hash_counts[hashed]
            hash_positions[hashed][count & (_HASH_SLOTS - 1)] = last_hashed
            hash_counts[hashed] = (count + 1) & 0xFF

    while len(output) < output_length:
        if control == 1:
            if cursor + 4 > compressed_length:
                raise ValueError("QuickLZ control word is truncated")
            control = struct.unpack_from("<I", source, cursor)[0]
            cursor += 4
            if not control & 0x80000000:
                raise ValueError("QuickLZ control word has no sentinel")

        if control & 1:
            if cursor + 2 > compressed_length:
                raise ValueError("QuickLZ reference is truncated")
            token = struct.unpack_from("<H", source, cursor)[0]
            slot = token & 3
            hashed = (token >> 5) & (_HASH_VALUES - 1)
            encoded_length = (token >> 2) & 7
            if encoded_length:
                match_length = encoded_length + 2
                cursor += 2
            else:
                if cursor + 3 > compressed_length:
                    raise ValueError("QuickLZ long reference is truncated")
                match_length = source[cursor + 2]
                cursor += 3
                if match_length < 3:
                    raise ValueError("QuickLZ long reference is too short")
            reference = hash_positions[hashed][slot]
            match_start = len(output)
            if reference is None or reference >= match_start:
                raise ValueError("QuickLZ reference points outside decoded history")
            if match_start + match_length > output_length:
                raise ValueError("QuickLZ reference exceeds advertised output")
            for offset in range(match_length):
                output.append(output[reference + offset])
            if last_hashed < match_start:
                update_hashes(match_start)
            last_hashed = len(output) - 1
        else:
            if cursor >= compressed_length:
                raise ValueError("QuickLZ literal is truncated")
            output.append(source[cursor])
            cursor += 1
            through = len(output) - 3
            if last_hashed < through:
                update_hashes(through)
        control >>= 1

    trailing = source[cursor:compressed_length]
    if len(trailing) > 4 or any(trailing):
        raise ValueError("QuickLZ block has trailing data")
    return bytes(output)
