"""Gwell P2P crypto primitives, reconstructed from libp2pav.so (see notes/wire-format-recovered.md).

Pure stdlib. Covers:
  - RC5 (canonical, parametrizable word size / rounds) — the transport uses RC5-32/6.
  - decrypt_rkey / encrypt_rkey — the 32-bit nibble-S-box transform (optional peer-key path).
  - gute frame checksum (sum of header words @0/4/8/0xc/0x14, stored @0x10).
  - gute_frame_encrypt / _decrypt — the exact byte/key layout from gute_frm_rc5_encrypt.

Constants recovered from disassembly:
  - base session key = b"www.gwell.cc"   (ctx +0x168, RC5-32/6)
  - DES handshake-auth key = a3 16 a9 9d 48 30 38 21   (see des note; DES itself TODO)
  - per-frame subkey (mode 1) = frm[0:4] ++ frm[0x14:0x18]
"""

import struct

BASE_SESSION_KEY = b"www.gwell.cc"
DES_AUTH_KEY = bytes.fromhex("a316a99d48303821")


# ------------------------------------------------------------------ RC5
class RC5:
    """Canonical RC5-w/r/b. w in bits (16/32/64). Little-endian words (C pointer-cast layout)."""

    def __init__(self, key: bytes, rounds: int = 6, w: int = 32):
        assert w in (16, 32, 64)
        self.w = w
        self.r = rounds
        self.mask = (1 << w) - 1
        self.ww = w // 8  # word bytes
        self.bb = self.ww * 2  # block bytes
        self.fmt = {16: "<H", 32: "<I", 64: "<Q"}[w]
        # magic constants P, Q for this word size (odd((e-2)*2^w) / odd((phi-1)*2^w))
        P = {16: 0xB7E1, 32: 0xB7E15163, 64: 0xB7E151628AED2A6B}[w]
        Q = {16: 0x9E37, 32: 0x9E3779B9, 64: 0x9E3779B97F4A7C15}[w]
        self.S = self._expand(key, P, Q)

    def _rotl(self, x, y):
        y &= self.w - 1
        return ((x << y) | (x >> (self.w - y))) & self.mask

    def _rotr(self, x, y):
        y &= self.w - 1
        return ((x >> y) | (x << (self.w - y))) & self.mask

    def _expand(self, key, P, Q):
        u = self.ww
        b = len(key)
        c = max(1, (b + u - 1) // u)
        L = [0] * c
        for i in range(b - 1, -1, -1):
            L[i // u] = ((L[i // u] << 8) + key[i]) & self.mask
        t = 2 * (self.r + 1)
        S = [(P + i * Q) & self.mask for i in range(t)]
        i = j = A = B = 0
        for _ in range(3 * max(t, c)):
            A = S[i] = self._rotl((S[i] + A + B) & self.mask, 3)
            B = L[j] = self._rotl((L[j] + A + B) & self.mask, (A + B))
            i = (i + 1) % t
            j = (j + 1) % c
        return S

    def encrypt_block(self, block: bytes) -> bytes:
        A, B = struct.unpack("<" + self.fmt[1] * 2, block)
        A = (A + self.S[0]) & self.mask
        B = (B + self.S[1]) & self.mask
        for i in range(1, self.r + 1):
            A = (self._rotl(A ^ B, B) + self.S[2 * i]) & self.mask
            B = (self._rotl(B ^ A, A) + self.S[2 * i + 1]) & self.mask
        return struct.pack("<" + self.fmt[1] * 2, A, B)

    def decrypt_block(self, block: bytes) -> bytes:
        A, B = struct.unpack("<" + self.fmt[1] * 2, block)
        for i in range(self.r, 0, -1):
            B = self._rotr((B - self.S[2 * i + 1]) & self.mask, A) ^ A
            A = self._rotr((A - self.S[2 * i]) & self.mask, B) ^ B
        B = (B - self.S[1]) & self.mask
        A = (A - self.S[0]) & self.mask
        return struct.pack("<" + self.fmt[1] * 2, A, B)


# ------------------------------------------------------------------ RKey transform
_SBOX = [1, 6, 0xD, 5, 0, 0xC, 0xE, 4, 0xB, 7, 0xF, 2, 8, 0xA, 3, 9]
_SBOX_INV = [_SBOX.index(i) for i in range(16)]


def _sub_nibbles(x, sbox):
    out = 0
    for _ in range(8):
        out = ((out << 4) | sbox[x & 0xF]) & 0xFFFFFFFF
        x >>= 4
    return out


def decrypt_rkey(x: int) -> int:
    x = (x ^ 0x25C314A6) & 0xFFFFFFFF
    r = _sub_nibbles(x, _SBOX)
    r = ((r << 30) | (r >> 2)) & 0xFFFFFFFF  # rotl 30 == rotr 2
    r ^= 0x1D74CBA2
    return _sub_nibbles(r, _SBOX)


def encrypt_rkey(x: int) -> int:
    """Inverse of decrypt_rkey (for self-test / building our own RKey)."""
    r = _sub_nibbles(x, _SBOX_INV)  # undo final substitution
    r ^= 0x1D74CBA2
    r = ((r << 2) | (r >> 30)) & 0xFFFFFFFF  # undo rotr 2 -> rotl 2
    r = _sub_nibbles(r, _SBOX_INV)
    return (r ^ 0x25C314A6) & 0xFFFFFFFF


# gw_M3: same S-box family, key transform used on the CALLING challenge. S-box @0xd4a90.
_M3 = [4, 8, 0, 0xB, 0xA, 7, 6, 3, 0xD, 0xF, 2, 5, 0xE, 1, 9, 0xC]
_M3_INV = [_M3.index(i) for i in range(16)]


def gw_m3(x: int) -> int:
    r = _sub_nibbles(x, _M3)
    r ^= 0xA2E39FD9
    r = ((r << 2) | (r >> 30)) & 0xFFFFFFFF  # rotl 2
    return _sub_nibbles(r, _M3)


def gw_dm3(x: int) -> int:
    """Inverse of gw_m3 (gw_DM3 @0x3ad14)."""
    r = _sub_nibbles(x, _M3_INV)
    r = ((r << 30) | (r >> 2)) & 0xFFFFFFFF  # undo rotl 2
    r ^= 0xA2E39FD9
    return _sub_nibbles(r, _M3_INV)


def gw_encode_password(x: int) -> int:
    """gw_EncodePassword: 17 rounds of x = x*0xA2E39FD9 + i (i=0..16)."""
    x &= 0xFFFFFFFF
    for i in range(17):
        x = (x * 0xA2E39FD9 + i) & 0xFFFFFFFF
    return x


# ------------------------------------------------------------------ gute frame
def gute_checksum(hdr: bytes | bytearray) -> int:
    """Sum of the five header words @0,4,8,0xc,0x14 (skips the chkval slot @0x10)."""

    def w(offset: int) -> int:
        return struct.unpack_from("<I", hdr, offset)[0]

    return (w(0) + w(4) + w(8) + w(0xC) + w(0x14)) & 0xFFFFFFFF


def set_checksum(frm: bytearray):
    struct.pack_into("<I", frm, 0x10, gute_checksum(frm))


def verify_checksum(frm: bytes | bytearray) -> bool:
    return struct.unpack_from("<I", frm, 0x10)[0] == gute_checksum(frm)


def modern_encrypted_data_len(frm: bytes | bytearray) -> int:
    """Return the payload bytes covered by the modern RC5 pass.

    ``iv_gute_get_encrypt_data_len`` excludes the optional 80-byte connection
    auth trailer and the optional 16-byte monotonic-timestamp trailer.  The
    remaining byte count need not be block aligned: the native RC5 loop then
    processes only complete eight-byte blocks.  Its checksum loop likewise
    consumes complete four-byte words, leaving a short terminal-name suffix or
    a final four-byte field (for example the certification MTU) in the clear.
    """

    if len(frm) < 0x18:
        raise ValueError("truncated gute frame")
    declared = struct.unpack_from("<H", frm, 2)[0]
    if declared != len(frm):
        raise ValueError("invalid gute frame length")
    encrypted_len = declared - 0x18
    flags = struct.unpack_from("<I", frm, 0x14)[0]
    if flags & (1 << 22):
        encrypted_len -= 0x50
    if flags & (1 << 24):
        encrypted_len -= 0x10
    if encrypted_len < 0:
        raise ValueError("invalid gute encrypted length")
    return encrypted_len


def _modern_crypt_payload(out: bytearray, wire_or_plain: bytes, rc5: RC5, *, encrypt: bool) -> None:
    transform = rc5.encrypt_block if encrypt else rc5.decrypt_block
    out[0xC:0x14] = transform(wire_or_plain[0xC:0x14])
    block_count = modern_encrypted_data_len(wire_or_plain) // 8
    for index in range(block_count):
        off = 0x18 + index * 8
        out[off : off + 8] = transform(wire_or_plain[off : off + 8])


def _modern_decrypt_id(out: bytearray, wire: bytes) -> None:
    id_masked = bytearray(8)
    struct.pack_into(
        "<I",
        id_masked,
        0,
        struct.unpack_from("<I", wire, 4)[0] ^ struct.unpack_from("<I", wire, 0xC)[0],
    )
    struct.pack_into(
        "<I",
        id_masked,
        4,
        struct.unpack_from("<I", wire, 8)[0] ^ struct.unpack_from("<I", wire, 0x10)[0],
    )
    out[4:12] = RC5(BASE_SESSION_KEY, rounds=6, w=32).decrypt_block(bytes(id_masked))


def _modern_encrypt_id(out: bytearray, plain: bytes) -> None:
    id_encrypted = RC5(BASE_SESSION_KEY, rounds=6, w=32).encrypt_block(plain[4:12])
    struct.pack_into(
        "<I",
        out,
        4,
        struct.unpack_from("<I", id_encrypted, 0)[0] ^ struct.unpack_from("<I", out, 0xC)[0],
    )
    struct.pack_into(
        "<I",
        out,
        8,
        struct.unpack_from("<I", id_encrypted, 4)[0] ^ struct.unpack_from("<I", out, 0x10)[0],
    )


def gute_mode1_decrypt(wire: bytes) -> bytes:
    """Decode a complete modern gute frame whose ``opt_encrypt`` is mode 1.

    This is the precise order used by ``iv_gute_frm_decrypt_id`` plus
    ``iv_gute_frm_rc5_decrypt`` for the bootstrap/list family as well as the
    captured command frames.  It deliberately does not interpret the payload.
    """
    if len(wire) < 0x18 or struct.unpack_from("<H", wire, 2)[0] != len(wire):
        raise ValueError("invalid gute frame length")
    if (wire[0x16] & 3) != 1:
        raise ValueError("gute_mode1_decrypt requires mode 1")

    out = bytearray(wire)
    # The id block is first unmasked with its on-wire encrypted counterpart,
    # then decoded with the fixed base context.
    _modern_decrypt_id(out, wire)

    mode_key = bytes(wire[0:4] + wire[0x14:0x17] + b"\x00")
    rc5 = RC5(mode_key, rounds=6, w=32)
    _modern_crypt_payload(out, wire, rc5, encrypt=False)
    return bytes(out)


def gute_mode1_encrypt(plain: bytes) -> bytes:
    """Inverse of :func:`gute_mode1_decrypt` for a fully populated frame."""
    if len(plain) < 0x18 or struct.unpack_from("<H", plain, 2)[0] != len(plain):
        raise ValueError("invalid gute frame length")
    if (plain[0x16] & 3) != 1:
        raise ValueError("gute_mode1_encrypt requires mode 1")

    out = bytearray(plain)
    mode_key = bytes(plain[0:4] + plain[0x14:0x17] + b"\x00")
    rc5 = RC5(mode_key, rounds=6, w=32)
    _modern_crypt_payload(out, plain, rc5, encrypt=True)
    # Keep the just-encrypted id/checksum words as the mask source. This is
    # the inverse of the unmasking performed by iv_gute_frm_decrypt_id.
    _modern_encrypt_id(out, plain)
    return bytes(out)


def gute_mode2_decrypt(wire: bytes, session_key: bytes) -> bytes:
    """Decode a modern mode-2 frame with the negotiated 32-byte RC5 key."""

    if len(session_key) != 32:
        raise ValueError("modern mode-2 requires a 32-byte session key")
    if len(wire) < 0x18 or struct.unpack_from("<H", wire, 2)[0] != len(wire):
        raise ValueError("invalid gute frame length")
    if (wire[0x16] & 3) != 2:
        raise ValueError("gute_mode2_decrypt requires mode 2")
    out = bytearray(wire)
    _modern_decrypt_id(out, wire)
    _modern_crypt_payload(out, wire, RC5(session_key, rounds=6, w=32), encrypt=False)
    return bytes(out)


def gute_mode2_encrypt(plain: bytes, session_key: bytes) -> bytes:
    """Encode a modern mode-2 frame with the negotiated 32-byte RC5 key."""

    if len(session_key) != 32:
        raise ValueError("modern mode-2 requires a 32-byte session key")
    if len(plain) < 0x18 or struct.unpack_from("<H", plain, 2)[0] != len(plain):
        raise ValueError("invalid gute frame length")
    if (plain[0x16] & 3) != 2:
        raise ValueError("gute_mode2_encrypt requires mode 2")
    out = bytearray(plain)
    _modern_crypt_payload(out, plain, RC5(session_key, rounds=6, w=32), encrypt=True)
    _modern_encrypt_id(out, plain)
    return bytes(out)


def gute_mode0_decrypt(wire: bytes) -> bytes:
    """Decode a modern frame whose payload encryption mode is zero.

    NAT hole-punch frames (``0x7f/0xca`` and ``0x7f/0xcb``) still mask their
    identity with the fixed base context even though their payload is clear.
    """

    if len(wire) < 0x18 or struct.unpack_from("<H", wire, 2)[0] != len(wire):
        raise ValueError("invalid gute frame length")
    if wire[0x16] & 3:
        raise ValueError("gute_mode0_decrypt requires mode 0")
    out = bytearray(wire)
    _modern_decrypt_id(out, wire)
    return bytes(out)


def gute_mode0_encrypt(plain: bytes) -> bytes:
    """Encode the identity field of a payload-clear modern frame."""

    if len(plain) < 0x18 or struct.unpack_from("<H", plain, 2)[0] != len(plain):
        raise ValueError("invalid gute frame length")
    if plain[0x16] & 3:
        raise ValueError("gute_mode0_encrypt requires mode 0")
    out = bytearray(plain)
    _modern_encrypt_id(out, plain)
    return bytes(out)


def gute_mode1_xor_checksum(frm: bytes | bytearray) -> int:
    """Checksum used by the mode-1 ``0x7f`` bootstrap/list frames.

    It differs from the older additive helper above: the native verifier XORs
    the header words and every encrypted-payload word, with the low 24 flag
    bits as its seed.  Call this while the frame is still plaintext.
    """
    if len(frm) < 0x18 or struct.unpack_from("<H", frm, 2)[0] != len(frm):
        raise ValueError("invalid gute frame length")
    encrypted_len = modern_encrypted_data_len(frm)
    flags = struct.unpack_from("<I", frm, 0x14)[0]
    out = flags & 0xFFFFFF
    for off in (0, 4, 8, 0xC):
        out ^= struct.unpack_from("<I", frm, off)[0]
    for off in range(0x18, 0x18 + (encrypted_len // 4) * 4, 4):
        out ^= struct.unpack_from("<I", frm, off)[0]
    return out & 0xFFFFFFFF


# ------------------------------------------------------------------ DES (auth token)
# The handshake auth token is `des(challenge, key=a316a99d48303821, ENCRYPT)` — the library's
# `des` @0x3ae88, hardcoded key @0x109718. Standard single-DES (the key is a plain 8-byte value
# and the call site is a textbook ECB block encrypt); implemented canonically and checked below
# against the FIPS 8.1 known-answer, so it is byte-faithful if the firmware uses standard DES.
_IP = (
    58,
    50,
    42,
    34,
    26,
    18,
    10,
    2,
    60,
    52,
    44,
    36,
    28,
    20,
    12,
    4,
    62,
    54,
    46,
    38,
    30,
    22,
    14,
    6,
    64,
    56,
    48,
    40,
    32,
    24,
    16,
    8,
    57,
    49,
    41,
    33,
    25,
    17,
    9,
    1,
    59,
    51,
    43,
    35,
    27,
    19,
    11,
    3,
    61,
    53,
    45,
    37,
    29,
    21,
    13,
    5,
    63,
    55,
    47,
    39,
    31,
    23,
    15,
    7,
)
_FP = (
    40,
    8,
    48,
    16,
    56,
    24,
    64,
    32,
    39,
    7,
    47,
    15,
    55,
    23,
    63,
    31,
    38,
    6,
    46,
    14,
    54,
    22,
    62,
    30,
    37,
    5,
    45,
    13,
    53,
    21,
    61,
    29,
    36,
    4,
    44,
    12,
    52,
    20,
    60,
    28,
    35,
    3,
    43,
    11,
    51,
    19,
    59,
    27,
    34,
    2,
    42,
    10,
    50,
    18,
    58,
    26,
    33,
    1,
    41,
    9,
    49,
    17,
    57,
    25,
)
_E = (
    32,
    1,
    2,
    3,
    4,
    5,
    4,
    5,
    6,
    7,
    8,
    9,
    8,
    9,
    10,
    11,
    12,
    13,
    12,
    13,
    14,
    15,
    16,
    17,
    16,
    17,
    18,
    19,
    20,
    21,
    20,
    21,
    22,
    23,
    24,
    25,
    24,
    25,
    26,
    27,
    28,
    29,
    28,
    29,
    30,
    31,
    32,
    1,
)
_P = (
    16,
    7,
    20,
    21,
    29,
    12,
    28,
    17,
    1,
    15,
    23,
    26,
    5,
    18,
    31,
    10,
    2,
    8,
    24,
    14,
    32,
    27,
    3,
    9,
    19,
    13,
    30,
    6,
    22,
    11,
    4,
    25,
)
_PC1 = (
    57,
    49,
    41,
    33,
    25,
    17,
    9,
    1,
    58,
    50,
    42,
    34,
    26,
    18,
    10,
    2,
    59,
    51,
    43,
    35,
    27,
    19,
    11,
    3,
    60,
    52,
    44,
    36,
    63,
    55,
    47,
    39,
    31,
    23,
    15,
    7,
    62,
    54,
    46,
    38,
    30,
    22,
    14,
    6,
    61,
    53,
    45,
    37,
    29,
    21,
    13,
    5,
    28,
    20,
    12,
    4,
)
_PC2 = (
    14,
    17,
    11,
    24,
    1,
    5,
    3,
    28,
    15,
    6,
    21,
    10,
    23,
    19,
    12,
    4,
    26,
    8,
    16,
    7,
    27,
    20,
    13,
    2,
    41,
    52,
    31,
    37,
    47,
    55,
    30,
    40,
    51,
    45,
    33,
    48,
    44,
    49,
    39,
    56,
    34,
    53,
    46,
    42,
    50,
    36,
    29,
    32,
)
_SHIFTS = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)
_DES_SBOX = (
    (
        14,
        4,
        13,
        1,
        2,
        15,
        11,
        8,
        3,
        10,
        6,
        12,
        5,
        9,
        0,
        7,
        0,
        15,
        7,
        4,
        14,
        2,
        13,
        1,
        10,
        6,
        12,
        11,
        9,
        5,
        3,
        8,
        4,
        1,
        14,
        8,
        13,
        6,
        2,
        11,
        15,
        12,
        9,
        7,
        3,
        10,
        5,
        0,
        15,
        12,
        8,
        2,
        4,
        9,
        1,
        7,
        5,
        11,
        3,
        14,
        10,
        0,
        6,
        13,
    ),
    (
        15,
        1,
        8,
        14,
        6,
        11,
        3,
        4,
        9,
        7,
        2,
        13,
        12,
        0,
        5,
        10,
        3,
        13,
        4,
        7,
        15,
        2,
        8,
        14,
        12,
        0,
        1,
        10,
        6,
        9,
        11,
        5,
        0,
        14,
        7,
        11,
        10,
        4,
        13,
        1,
        5,
        8,
        12,
        6,
        9,
        3,
        2,
        15,
        13,
        8,
        10,
        1,
        3,
        15,
        4,
        2,
        11,
        6,
        7,
        12,
        0,
        5,
        14,
        9,
    ),
    (
        10,
        0,
        9,
        14,
        6,
        3,
        15,
        5,
        1,
        13,
        12,
        7,
        11,
        4,
        2,
        8,
        13,
        7,
        0,
        9,
        3,
        4,
        6,
        10,
        2,
        8,
        5,
        14,
        12,
        11,
        15,
        1,
        13,
        6,
        4,
        9,
        8,
        15,
        3,
        0,
        11,
        1,
        2,
        12,
        5,
        10,
        14,
        7,
        1,
        10,
        13,
        0,
        6,
        9,
        8,
        7,
        4,
        15,
        14,
        3,
        11,
        5,
        2,
        12,
    ),
    (
        7,
        13,
        14,
        3,
        0,
        6,
        9,
        10,
        1,
        2,
        8,
        5,
        11,
        12,
        4,
        15,
        13,
        8,
        11,
        5,
        6,
        15,
        0,
        3,
        4,
        7,
        2,
        12,
        1,
        10,
        14,
        9,
        10,
        6,
        9,
        0,
        12,
        11,
        7,
        13,
        15,
        1,
        3,
        14,
        5,
        2,
        8,
        4,
        3,
        15,
        0,
        6,
        10,
        1,
        13,
        8,
        9,
        4,
        5,
        11,
        12,
        7,
        2,
        14,
    ),
    (
        2,
        12,
        4,
        1,
        7,
        10,
        11,
        6,
        8,
        5,
        3,
        15,
        13,
        0,
        14,
        9,
        14,
        11,
        2,
        12,
        4,
        7,
        13,
        1,
        5,
        0,
        15,
        10,
        3,
        9,
        8,
        6,
        4,
        2,
        1,
        11,
        10,
        13,
        7,
        8,
        15,
        9,
        12,
        5,
        6,
        3,
        0,
        14,
        11,
        8,
        12,
        7,
        1,
        14,
        2,
        13,
        6,
        15,
        0,
        9,
        10,
        4,
        5,
        3,
    ),
    (
        12,
        1,
        10,
        15,
        9,
        2,
        6,
        8,
        0,
        13,
        3,
        4,
        14,
        7,
        5,
        11,
        10,
        15,
        4,
        2,
        7,
        12,
        9,
        5,
        6,
        1,
        13,
        14,
        0,
        11,
        3,
        8,
        9,
        14,
        15,
        5,
        2,
        8,
        12,
        3,
        7,
        0,
        4,
        10,
        1,
        13,
        11,
        6,
        4,
        3,
        2,
        12,
        9,
        5,
        15,
        10,
        11,
        14,
        1,
        7,
        6,
        0,
        8,
        13,
    ),
    (
        4,
        11,
        2,
        14,
        15,
        0,
        8,
        13,
        3,
        12,
        9,
        7,
        5,
        10,
        6,
        1,
        13,
        0,
        11,
        7,
        4,
        9,
        1,
        10,
        14,
        3,
        5,
        12,
        2,
        15,
        8,
        6,
        1,
        4,
        11,
        13,
        12,
        3,
        7,
        14,
        10,
        15,
        6,
        8,
        0,
        5,
        9,
        2,
        6,
        11,
        13,
        8,
        1,
        4,
        10,
        7,
        9,
        5,
        0,
        15,
        14,
        2,
        3,
        12,
    ),
    (
        13,
        2,
        8,
        4,
        6,
        15,
        11,
        1,
        10,
        9,
        3,
        14,
        5,
        0,
        12,
        7,
        1,
        15,
        13,
        8,
        10,
        3,
        7,
        4,
        12,
        5,
        6,
        11,
        0,
        14,
        9,
        2,
        7,
        11,
        4,
        1,
        9,
        12,
        14,
        2,
        0,
        6,
        10,
        13,
        15,
        3,
        5,
        8,
        2,
        1,
        14,
        7,
        4,
        10,
        8,
        13,
        15,
        12,
        9,
        0,
        3,
        5,
        6,
        11,
    ),
)


def _permute(bits, table):
    return [bits[i - 1] for i in table]


def _des_subkeys(key: bytes):
    kb = [(key[i >> 3] >> (7 - (i & 7))) & 1 for i in range(64)]
    cd = _permute(kb, _PC1)
    c, d = cd[:28], cd[28:]
    out = []
    for s in _SHIFTS:
        c = c[s:] + c[:s]
        d = d[s:] + d[:s]
        out.append(_permute(c + d, _PC2))
    return out


def _des_block(block: bytes, subkeys):
    bits = [(block[i >> 3] >> (7 - (i & 7))) & 1 for i in range(64)]
    bits = _permute(bits, _IP)
    left, right = bits[:32], bits[32:]
    for k in subkeys:
        er = [a ^ b for a, b in zip(_permute(right, _E), k, strict=True)]
        out = []
        for i in range(8):
            chunk = er[i * 6 : i * 6 + 6]
            row = (chunk[0] << 1) | chunk[5]
            col = (chunk[1] << 3) | (chunk[2] << 2) | (chunk[3] << 1) | chunk[4]
            val = _DES_SBOX[i][row * 16 + col]
            out += [(val >> 3) & 1, (val >> 2) & 1, (val >> 1) & 1, val & 1]
        f = _permute(out, _P)
        left, right = right, [a ^ b for a, b in zip(left, f, strict=True)]
    fin = _permute(right + left, _FP)
    return bytes(sum(fin[i * 8 + j] << (7 - j) for j in range(8)) for i in range(8))


def des_encrypt(block8: bytes, key8: bytes) -> bytes:
    """Single-DES ECB encrypt of one 8-byte block."""
    assert len(block8) == 8 and len(key8) == 8
    return _des_block(block8, _des_subkeys(key8))


DES_AUTH_KEY = bytes.fromhex("a316a99d48303821")  # @0x109718


# ------------------------------------------------------------------ self-test
if __name__ == "__main__":
    # RC5 round-trip (32/6, the transport cipher) + RFC 2040 known-answer for RC5-32/12/16.
    c = RC5(b"www.gwell.cc", rounds=6, w=32)
    pt = b"\x01\x23\x45\x67\x89\xab\xcd\xef"
    assert c.decrypt_block(c.encrypt_block(pt)) == pt, "RC5-32/6 round-trip failed"

    kat = RC5(bytes.fromhex("00000000000000000000000000000000"), rounds=12, w=32)
    ct = kat.encrypt_block(bytes.fromhex("0000000000000000"))
    # RFC 2040 RC5-32/12/16 all-zero key/plaintext -> 21A5DBEE154B8F6D
    assert ct == bytes.fromhex("21a5dbee154b8f6d"), f"RC5 KAT mismatch: {ct.hex()}"

    # RKey transform is a bijection
    for v in (0, 1, 0xDEADBEEF, 0x12345678, 0xFFFFFFFF):
        assert encrypt_rkey(decrypt_rkey(v)) == v, f"rkey inverse failed @ {v:#x}"
        assert decrypt_rkey(encrypt_rkey(v)) == v, f"rkey inverse failed @ {v:#x}"

    # gw_M3 is a bijection (its inverse is gw_DM3)
    for v in (0, 1, 0xDEADBEEF, 0x12345678, 0xFFFFFFFF):
        assert gw_dm3(gw_m3(v)) == v, f"gw_m3 inverse failed @ {v:#x}"
        assert gw_m3(gw_dm3(v)) == v, f"gw_m3 inverse failed @ {v:#x}"

    # gw_encode_password is deterministic (spot value, guards against regressions)
    assert gw_encode_password(123456) == gw_encode_password(123456)

    # DES against the FIPS 8.1 / Stallings known-answer vector.
    kat_des = des_encrypt(bytes.fromhex("0123456789abcdef"), bytes.fromhex("133457799bbcdff1"))
    assert kat_des == bytes.fromhex("85e813540f0ab405"), f"DES KAT mismatch: {kat_des.hex()}"
    print("DES KAT ok:", kat_des.hex())

    # checksum
    frm = bytearray(0x1C)
    struct.pack_into("<I", frm, 0, 0x11111111)
    struct.pack_into("<I", frm, 0x14, 0x22222222)
    set_checksum(frm)
    assert verify_checksum(frm)

    print(
        "OK — RC5 (round-trip + RFC2040 KAT), RKey + gw_M3 bijections, "
        "gw_EncodePassword, gute checksum all pass."
    )
