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

Key source and block/remainder handling are mapped above. Key expansion was
subsequently checked against the independent vectors below. Next: native block
transform/real-record interoperability, then the type-specific RTC unpackers
with bounded record/fragment ownership. Keep relay certification/session
provenance and teardown as independent gates. Neither this boundary nor the
keepalive encoder homologates HD, relay media reception or LAN-only operation.

## Independent key-schedule regression vectors

`rc5_ctx_setkey` (`0x261454`) selects helper `0x2617ac` for context word size 32
at `0x2614d8–0x2614e4`. That helper copies the eight key bytes into little-endian
u32 words, initializes 14 schedule words from `0xb7e15163` with increment
`0x9e3779b9`, then performs 42 mixing iterations. The rotations are
`ROR32(S[i] + A + B, 29)` and `ROR32(L[j] + A + B, -(A + B))`, with u32
arithmetic; indices wrap modulo 14 and 2 respectively (`0x26192c–0x2619c4`).

Three complete 14-word schedules were calculated separately with a JavaScript
unsigned-32-bit transcription of those operations, without calling the production
Python cipher: all-zero key, ascending `00..07`, and all-`ff`. The static vectors
in `tests/test_vendor_push_rc5_schedule.py` match `RC5(key, rounds=6, w=32).S`.
The 45 schedule/span tests passed under 256 MiB/no-swap/50%-CPU limits (68.1 MiB
peak); Ruff passed. This is an independently calculated key-expansion check,
not execution of the native binary or a captured-media decryption test.

## Independent block transform and offline decryption

SDK `rc5_ctx_enc` at `0x261ca4` selects the eight-byte helper `0x261ea4`;
`rc5_ctx_dec` at `0x26218c` selects `0x262390`. The former adds S[0]/S[1]
to two little-endian u32 words, then performs six canonical RC5 rounds with
data-dependent rotations. The latter reverses the rounds and subtracts the
whitening words. Variable AArch64 shift counts are masked to five bits.

Four fixed block vectors for synthetic key `00..07` were independently computed
with JavaScript unsigned-u32 arithmetic and the previously derived schedule.
They include zero/all-one/ascending plaintext and a block cancelling whitening
to exercise zero rotation counts. Both directions match the existing Python
cipher; these are not native execution or captured-media fixtures.

`push_rtc_crypto.decrypt_selective_rtc` is an **offline-only** helper, with no
production caller. It validates the bounded record before constructing RC5,
requires an explicit eight-byte immutable key, transforms full blocks only,
and preserves the clear prefix and 1–7 byte remainder. All six known record
types are covered with zero/one/four blocks and every remainder size. Short
spans remain unchanged; that is not evidence of valid media. RC5 supplies no
integrity or authentication and cannot detect a wrong key.

205 focused cipher/span tests passed under 256 MiB/no-swap/50%-CPU limits,
with 76.4 MiB peak. No camera connection, runtime capability change, or
deployment occurred. Remaining gates: real-record interoperability, strict
type-specific unpacking, authenticated relay/session ownership and teardown.

## Header-only RTC record: first unpacking checkpoint

`packing_rtcfrm_header_only` (`0x17f3fc`, 196 bytes) writes type `0x81`,
a ten-byte prefix, and a count at byte 9. It appends count ×20 bytes from
the source payload after its first two bytes. The length at byte 4 comes
from the source vector's byte count, not an independently recomputed count.
Do not assign codec/stream meanings to these opaque twenty-byte entries yet.

`unpacking_rtcfrm_avhead` (`0x17d7d4`, 852 bytes) checks at least ten ring
bytes, copies the prefix, reads byte 9, then copies count twenty-byte entries.
Its output payload starts with zero and count, followed by those entries.
The output discriminator is 2, or 6 when the optional context's first byte
equals `0x83` (`0x17d958–0x17d96c`). Those are SDK output discriminators,
not proven video/audio codec identifiers.

This function itself does not recheck the available ring size for the complete
count before the entry loop. Caller-level validation remains to be traced;
this observation alone is **not** proof of a reachable SDK vulnerability.
A future local parser must validate the complete `10 + 20 * count` extent
before reading or publishing entries, independently of caller assumptions.
Length agreement with the enclosing record and the entry field meanings still
need evidence. No speculative parser or capability was enabled. These two
symbol-bounded disassemblies peaked at 27 MiB under a 128 MiB/no-swap cap.

## Dispatcher bounds and strict header entry parser

`trans_proto_v2::unpacking_data` (`0x17ce10`, 1352 bytes) peeks eight
bytes, checks `(type & 0xff80) == 0x80` (`0x17cf0c–0x17cf18`), rejects
zero body length and waits for `u32(body_length) + 8` ring bytes before
dispatch (`0x17cf1c–0x17cf38`). The addition is performed in a 64-bit
register. At `0x17d144–0x17d160`, types 0x81/0x83 receive the optional
type context. This validates declared outer availability, **not** agreement
between that length and the header entry count read by the callee.

Added offline `push_rtc_headers.parse_rtc_header_entries`, accepting only a
complete decrypted type-0x81 record. It reuses bounded outer length checking
and requires exact `len(frame) == 10 + 20 * frame[9]` before returning an
immutable tuple of opaque entries. Zero through 255 entries are structurally
representable; this does not certify their media semantics. Type 0x83 is
deliberately not accepted until its dispatch mapping is independently checked.
No byte-scanning recovery, network, codec inference or production wiring.

Regression coverage includes zero/one/two/255 entries, mismatched count with
otherwise valid outer length, truncation, trailing data, forged huge lengths,
mutable input and unsupported types. The dispatcher audit peaked at 27.3 MiB.
Next: resolve the dispatch table and entry consumers before interpreting codecs;
real-media interoperability and relay session gates remain pending.

### Resolved dispatch table

Read the eight 16-byte table entries at `0x2a1bd0`, resolving pointer slots
through ELF `R_AARCH64_ABS64` relocations (symbol value plus addend, not the
zero bytes stored in the unrelocated pointer slots):

| RTC type | Handler address | SDK handler suffix |
| --- | --- | --- |
| 0x80 | 0x17d358 | avdata |
| 0x81 | 0x17d7d4 | avhead |
| 0x82 | 0x17db28 | usrdata |
| 0x83 | 0x17d7d4 | avhead |
| 0xf0 | 0x17dda8 | frag_begin |
| 0xf1 | 0x17e1b0 | frag_data |
| 0xf2 | 0x17e47c | frag_end |
| 0xf3 | 0x17e878 | frag_error |

This closes the earlier 0x83 routing uncertainty: the header parser now accepts
both 0x81 and 0x83. Callers must retain the original record type because output
discriminators 2/6 differ; a shared entry layout does not make them semantically
interchangeable. The selective decryption helper still rejects 0xf0/0xf3:
dispatch support is not proof of encryption handling. Fragment assembly remains
unimplemented. Table inspection peaked at 20.2 MiB, with no network access.
The first parser checkpoint passed 229 focused tests at 79.5 MiB/no swap.

Fragment follow-up: all four handlers are now traced, with a separate bounded
offline envelope parser and assembly helper. See [fragment ownership and
deliberate SDK divergences](yoosee-push-fragments.md). This does not enable
live media, recursive parsing, or decryption of previously unsupported types.

## AV payload branch evidence (not codec identification)

`unpacking_rtcfrm_avdata` (`0x17d358`, 1148 bytes) requires a 24-byte
prefix and at least `u32(+4) - 16` further bytes. At `0x17d460` it tests
byte +8: bit 0 selects grouped subframes; otherwise bit 1 selects a single
payload. Neither bit set skips payload extraction in this handler; that is
not a valid-media guarantee we should copy.

Single-payload output has SDK discriminator 1, copies u64(+16), extracts
bit 2 of byte +8, subtracts one from byte +10, and copies byte +12.
Grouped output has discriminator 0 and uses the low nibble of byte +11 as
the subframe count. Each subframe has an eight-byte prefix: low u16 is payload
length, bits 16..47 form an unsigned delta added to u64(+16). Its last two
prefix bytes are not interpreted in this routine. Each output copies byte
+10 minus one and byte +12; its other integer flag is set to 1.

Evidence spans: single metadata `0x17d4c0–0x17d4e8`; grouped count
`0x17d534–0x17d560`; per-subframe bounds `0x17d564–0x17d5ec`; grouped
metadata `0x17d630–0x17d65c`. Time units, codec meanings, channel semantics
and output discriminator names still require consumer evidence. The handler's
subframe checks use remaining ring bytes, not an explicit per-record slice;
our future payload parser must never read into a subsequent RTC record.
Symbol-only analysis peaked at 27.2 MiB under a 128 MiB/no-swap cap.

### Offline AV subframe parsing

`push_rtc_av.parse_rtc_av_units` now implements the two observed branches for
one complete plaintext type-0x80 record, capped locally at 256 KiB. It preserves
the SDK discriminator, raw u64 clock arithmetic (including wrap), raw flag,
byte +10 minus one and byte +12, without naming codecs/channels/time units.
Payloads are excluded from dataclass representations. Group count is the low
nibble (maximum 15); bit 0 takes precedence when bits 0 and 1 are both set.

Unlike the SDK ring access, each subframe prefix/length must remain within this
record, and grouped parsing must consume it exactly. Empty groups and unknown
branches are rejected. Zero-byte individual payloads remain structurally allowed,
not homologated media. No partial units escape if a later unit is truncated.
Single/grouped vectors, clock wrap, malformed inputs, maximum record and count
boundaries are synthetic checks, not real media interoperability.

## Consumer-confirmed AV semantics

`DataTypeName` (`0x16a434`, 936 bytes) initializes explicit labels:
0 = `AU_DATA`, 1 = `VI_DATA`, 2 = `HEADER_ONLY`, 3 = `USR_DATA`,
4 = `SEQUENCE_USR_DATA`, 5 = `FILE_DATA`, 6 = `HEADER_ENC`.
Assignments for audio/video are at `0x16a4e0–0x16a518`; header-encrypted
at `0x16a5a4–0x16a5c4`. These are output discriminators, not RTC wire types.

`BasePlayer::Impl::on_rcv_data` (`0x11a5b4`, 2520 bytes) consumes `Unpacked`:
u64 +0x20 is logged as `pts`, u32 +0x28 as `key`, and byte +0x30 as
`seq_num` (`0x11a644–0x11a6d8`). The video path uses +0x28 to identify
key frames, with payload inspection only when its high bit is set
(`0x11ab0c–0x11ab6c`). Our RTC parser produces the observed boolean bit,
not that fallback sentinel. The key-frame property remains a metadata hint,
not validation of the compressed stream. Audio's fixed integer 1 must not
be exposed as a video key-frame assertion.

The offline unit now exposes `media_kind`, `pts_raw`, `is_key_frame` (None
for audio/unknown discriminator), and `sequence_number`, retaining raw fields.
No PTS scaling or codec mapping is inferred. The eight-bit sequence label is
not an ordering/retransmission mechanism implemented by this parser.

The same consumer iterates twenty-byte header entries: byte +1 equal to 1
feeds `VideoFormat`, equal to 2 feeds `AudioFormat`. For video it reads a
float32 frame rate at +8 (`0x11a838`) and substitutes 15 when below 5
(`0x11a898–0x11a8a0`). We do not copy this silent correction. It also reads
u16 +4/+6, byte +12, and byte +2 (zero maps to index zero, otherwise minus
one). Audio reads u32 +4, u16 +8, and bytes +10..13; their individual field
names/codec enums still need the format consumers. Do not assume video and
audio indexes share the same numbering: this consumer offsets audio indexes
by the video map size (`0x11a9d8–0x11a9ec`, `0x11aad8–0x11aaf0`).

309 focused tests passed at 93.9 MiB/no swap under 256 MiB/50%-CPU limits;
Ruff passed. Symbol-sized audits peaked at 40.6 MiB under a 128 MiB cap.
No media capture, device command, production wiring or deployment occurred.
Next: format consumers for codec enums and PTS time base, then real-media
fixtures. Driver capabilities remain unchanged by this static SDK evidence.
