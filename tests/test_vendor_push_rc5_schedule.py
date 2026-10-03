"""Key schedules derived independently from SDK 6.45 ARM64, not live keys.

0x2617ac: memcpy eight bytes into two LE u32s; 14 P/Q words; 42 mixes
using ROR32(sum,29) then ROR32(sum+L,-sum). Constants below were calculated
with a separate JavaScript unsigned-32-bit transcription, not RC5._expand.
This verifies key expansion only, not native execution or media interoperability.
"""

import pytest

from backend.app.drivers.yoosee.p2p.crypto import RC5


@pytest.mark.parametrize("key_hex,expected", [
    ("0000000000000000", [
        0xab5be568, 0x445f85a3, 0x59b6fe3f, 0x6d774143, 0x98669d57,
        0x62276e1e, 0xfe65de6c, 0xb04c9673, 0xe00214d6, 0xc63e0beb,
        0x92f73886, 0x3fdc9381, 0x3d50893e, 0xe8e8dc17,
    ]),
    ("0001020304050607", [
        0x1a8ad0ce, 0xfe87b77b, 0x8140b2a6, 0x36a5d674, 0x577344d6,
        0x764ccae0, 0x9b39ab6c, 0x10e3445b, 0xad0733b2, 0x57469d1f,
        0xb13bb80b, 0xaa8e7edf, 0x964864d9, 0x70df94e9,
    ]),
    ("ffffffffffffffff", [
        0xfec6a41f, 0x35eda274, 0xea48cb9e, 0xd0562d01, 0x32a652ad,
        0x2c42fb00, 0x40f6ed78, 0x33aaf146, 0xaa9d7408, 0x2c9172cc,
        0x974f72a4, 0x768a3adf, 0xc17d230f, 0x0c0e59b4,
    ]),
])
def test_sdk_645_eight_byte_key_schedule(key_hex, expected):
    cipher = RC5(bytes.fromhex(key_hex), rounds=6, w=32)
    assert cipher.S == expected
    assert cipher.bb == 8
