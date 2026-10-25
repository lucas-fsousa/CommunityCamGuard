"""Bounded HEVC/AAC MPEG-TS input for one local FFmpeg, with explicit microsecond PTS.

No network, camera identity, codecs inferred from brand, threads or persistent state.
Caller must validate the actual native header and establish its timestamp units.
"""

VIDEO_PID = 0x101
AUDIO_PID = 0x102


def crc32(data: bytes) -> bytes:
    value = 0xFFFFFFFF
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
    return value.to_bytes(4, "big")


def pts_field(value: int) -> bytes:
    return bytes((0x21 | ((value >> 29) & 14), (value >> 22) & 255,
                  ((value >> 14) & 254) | 1, (value >> 7) & 255, ((value << 1) & 254) | 1))


class AvTransport:
    def __init__(self, origin_us: int):
        if type(origin_us) is not int or not 0 <= origin_us < 1 << 64:
            raise ValueError("validated native timestamp required")
        self._origin = origin_us
        self._continuity: dict[int, int] = {}
        self._started = False

    def _packets(self, pid: int, payload: bytes, *, pcr: int | None = None) -> bytes:
        packets = []
        first = True
        while payload:
            capacity = 176 if first and pcr is not None else 184
            part, payload = payload[:capacity], payload[capacity:]
            continuity = self._continuity.get(pid, 0)
            self._continuity[pid] = (continuity + 1) & 15
            header = bytes((0x47, (0x40 if first else 0) | (pid >> 8), pid & 255,
                            (0x30 if len(part) < 184 else 0x10) | continuity))
            adaptation = b""
            if len(part) < 184:
                length = 183 - len(part)
                adaptation = bytes((length,))
                if length:
                    if first and pcr is not None:
                        adaptation += b"\x10" + ((pcr << 15) | (0x3F << 9)).to_bytes(6, "big")
                        adaptation += b"\xff" * (length - 7)
                    else:
                        adaptation += b"\0" + b"\xff" * (length - 1)
            packets.append(header + adaptation + part)
            first = False
        return b"".join(packets)

    def _tables(self) -> bytes:
        pat = bytes.fromhex("00b00d0001c100000001e100")
        pmt = bytes.fromhex("02b0170001c10000e101f00024e101f0000fe102f000")
        return (self._packets(0, b"\0" + pat + crc32(pat))
                + self._packets(0x100, b"\0" + pmt + crc32(pmt)))

    def _pes(self, payload: bytes, timestamp_us: int, *, video: bool) -> bytes:
        if (type(payload) is not bytes or not 0 < len(payload) <= (1024 * 1024 if video else 65527)
                or type(timestamp_us) is not int or not self._origin <= timestamp_us < 1 << 64):
            raise ValueError("bounded ordered native access unit required")
        pts = (((timestamp_us - self._origin) * 9) // 100) & ((1 << 33) - 1)
        size = 0 if video else len(payload) + 8
        pes = (b"\0\0\1" + bytes((0xE0 if video else 0xC0,)) + size.to_bytes(2, "big")
               + b"\x80\x80\x05" + pts_field(pts) + payload)
        tables = b""
        if not self._started:
            tables, self._started = self._tables(), True
        return tables + self._packets(VIDEO_PID if video else AUDIO_PID, pes, pcr=pts if video else None)

    def video(self, payload: bytes, timestamp_us: int) -> bytes:
        return self._pes(payload, timestamp_us, video=True)

    def audio(self, payload: bytes, timestamp_us: int) -> bytes:
        return self._pes(payload, timestamp_us, video=False)
