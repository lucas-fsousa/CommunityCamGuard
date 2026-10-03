# Relay RTC receive boundary — SDK 6.45

2026-10-03, ARM64 binary pinned in
[platform provenance](yoosee-platform-sdk-versions.md). Offline inspection only.

## Outer frame to RTC record

`iv_rcv_rtc_frame` (`0x27b938`, 92 bytes) passes type-8 frame bytes after its
20-byte outer header, with the u16 body length from outer `+4`, directly to
`iv_rcv_rtc_data`. It does not independently authenticate or validate that body.
UDP has a distinct KCP entry path; this trace is not a universal UDP decoder.

`iv_rcv_rtc_data` (`0x27bc8c`, 640 bytes) uses session byte `+0x20` to select
encryption handling with context at `+0x18`:

* Value 2 calls `iv_enc_dec_rtc_data` with the actual supplied record size.
* Value 1 calls `rc5_enc_dec_data` on the record using its embedded u32 length
  at `+4`, plus eight bytes. Key setup and safe cipher bounds remain to map.
* Other values fall through in this function; they are not evidence that a
  future client may guess plaintext mode.

These are **encryption settings**, not device-platform enums. After that step
the SDK compares actual size with `u32le(+4) + 8`, but logs a mismatch and still
continues dispatch. A future receiver must reject instead, before unsafe memory
access or media buffering. Type `0x82` is written to the command ring at `+0x380`;
other types go to the AV ring at `+0x378`. Unknown types must not automatically
be treated as video in our implementation.

## Selective encryption span

`iv_enc_dec_rtc_data` (`0x279034`, 308 bytes) reads the u16 record type and passes
these spans to `rc5_enc_dec_data`:

| Record type | Clear prefix / cipher start | Evidence |
| --- | --- | --- |
| `0x80` | 24 bytes | `0x279054–0x279090` |
| `0x81`, `0x82`, `0x83` | 8 bytes | `0x27909c–0x2790f8` |
| `0xf1`, `0xf2` | 20 bytes | `0x279104–0x279154` |

The requested byte count is actual size minus prefix. This does not establish
RC5 rounds, key derivation, block/remainder handling or complete media schemas.
Unknown types do not invoke the cipher in the inspected helper; our offline
boundary rejects them instead of inventing plaintext support.

`push_rtc.selective_rtc_cipher_span` implements only strict preconditions and
returns `(offset, byte_count)`. It requires immutable bytes, exact embedded
length and enough bytes for the clear prefix. Its local size ceiling is
`0x8400 - 20`, aligned with our outer TCP framing limit. It does not decrypt,
select session mode, load a key, parse AV contents or connect to anything.
An empty cipher span passing these checks is not proof of a valid media record.

Synthetic tests cover every mapped type, short/partial prefixes, forged lengths,
oversized records, unknown types and non-block-aligned spans without guessing
cipher rounding. Eight-module relay suite: **187 tests passed**, peak 76.9 MiB,
no swap under 256 MiB/50%-CPU limits; Ruff passed. Symbol-sized disassembly stayed
below 28 MiB under 128 MiB caps. No runtime deployment or camera traffic occurred.

## Cipher/key provenance follow-up

`iv_push_session_app_new` stores its E4 argument x2 at stack `+0x50`
(`0x277e48`). It copies E4 byte `+0x1b` into session encryption setting `+0x20`
at `0x277f20–0x277f2c`, and E4 bytes `+0x20..+0x27` into session `+0x10`
at `0x277f50–0x277f5c`. It calls `rc5_ctx_new(8, 6)` at `0x277f60–0x277f6c`,
stores the result at session `+0x18`, then calls `rc5_ctx_setkey` with the eight
bytes at session `+0x10` (`0x277f70–0x277f8c`). These key bytes are distinct
from the variable relay-certification token at E4 `+0x88`; do not substitute it.

`rc5_ctx_new` (`0x26130c`, 292 bytes) records the second argument as rounds at
context byte 0 and the first at byte 1; context byte 2 is first argument ×4
(`0x2613dc–0x261400`). For this call the values are 6, 8 and 32, consistent with
six rounds, eight-byte blocks and 32-bit words. Native key expansion and actual
encrypted-record fixtures remain independent verification steps before claiming
interoperable decryption with the existing Python RC5 implementation.

`rc5_enc_dec_data` (`0x278f1c`, 280 bytes) rejects nonpositive byte counts, then
processes **floor(count / 8)** independent eight-byte blocks through `rc5_ctx_enc`
or `rc5_ctx_dec`. There is no IV/chaining or padding in this wrapper, and a final
1–7 bytes remain unchanged. The selective-mode wrapper ignores its return value,
including for an empty span; an empty span alone is not valid media evidence.

No key was extracted from a live capture, printed, retained, or used to decrypt
media. These findings come exclusively from instructions and field offsets.

## Next evidence

Key source and block/remainder handling are mapped above. Next: verify native
key expansion against independent vectors, then the type-specific RTC unpackers
with bounded record/fragment ownership. Keep relay certification/session
provenance and teardown as independent gates. Neither this boundary nor the
keepalive encoder homologates HD, relay media reception or LAN-only operation.
