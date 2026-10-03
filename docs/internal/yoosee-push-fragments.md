# Offline RTC fragment ownership

Evidence: SDK 6.45 ARM64 `libiotvideomulti.so`, pinned by SHA-256 in
[RTC evidence](yoosee-push-rtc.md). No camera traffic or native binary execution.

## Wire evidence

The four fragment handlers all read a twenty-byte prefix and consume
`u32(+4) - 12` additional bytes (total record size is body length +8).
They use signed i32 at +8 as `frag_id`. Bytes +12..19 are not a proven
sequence number; only the error handler labels u32(+12) as an error code.

| Type | Handler | Observed action |
| --- | --- | --- |
| 0xf0 | 0x17dda8, 1032 bytes | Replace/create ID's ring, enqueue payload |
| 0xf1 | 0x17e1b0, 716 bytes | Require existing ID, append payload |
| 0xf2 | 0x17e47c, 1020 bytes | Append, invoke parser virtual slot, erase ID |
| 0xf3 | 0x17e878, 752 bytes | Log ID/error, discard payload, erase ID |

Begin copies the prefix at `0x17de30`, takes body length at `0x17de50`,
and subtracts 12 at `0x17de64`. It logs duplicate IDs and constructs a fresh
ring with initial size 4096 (`0x17dfc8`); **initial capacity is not a bound**.
Data appends at `0x17e410`. End appends at `0x17e6e8`, invokes a virtual
parser at `0x17e72c`, warns if bytes remain, and erases at `0x17e7fc`.
Error reads ID/error at `0x17e98c` and erases at `0x17eafc`.
Data/end/error return -2 for unknown IDs. No sequence comparison, time expiry,
or aggregate memory cap is visible in these four handlers; this is not a
claim that no other SDK/transport layer supplies such protection.

## Local implementation and deliberate restrictions

`push_rtc_fragments.py` validates an immutable complete plaintext record before
copying payload, preserves +12..19 as opaque metadata, and exposes an error
code only for 0xf3. Record size is locally capped at `0x8400 - 20`.
This does not expand selective decryption support to 0xf0/0xf3.

`push_rtc_assembly.py` is separate, offline and unused by production. It accepts
already ordered records from **one source/session per instance**. It allows
four pending IDs, 256 KiB aggregate payload and 256 records per ID. These are
local protective policies, not advertised camera limits. Duplicate begins,
unknown IDs, malformed records and limit violations clear all pending state
and close the instance permanently. Unlike the SDK, duplicate begin is not
silently replaced. Error removes only its matching assembly; end returns
opaque concatenated bytes and retires the ID. No recursive parser is called.

There is no timer/network/async owner here: cancellation, EOF and reconnect
must call `close()` and use a new instance. Bounded retained bytes do not imply
bounded lifetime. Ordered delivery is a prerequisite, not something this
helper verifies; missing/duplicate data cannot be inferred from unknown fields.
Never treat returned bytes as validated media or accept them across generations.

267 focused tests passed (82.3 MiB peak, no swap, 256 MiB/50%-CPU cap); Ruff
passed. Coverage includes signed IDs, interleaving, error cleanup, duplicate
begin, missing begin, malformed lengths, limits even for zero-byte records,
explicit close and source isolation. Symbol-sized SDK audits peaked at 27.1 MiB.

Next: resolve the end-handler virtual target, validate inner record boundaries
without recursive/unbounded parsing, and map AV metadata consumers. Real media,
session authentication and production lifecycle integration remain unverified.

## End-handler virtual target confirmed

ELF `_ZTVN8iotvideo14trans_proto_v2E` is at `0x2a1cc0` (32 bytes).
Its function slot at +0x10 resolves through the symbol relocation to
`trans_proto_v2::unpacking_data` (`0x17ce10`); +0x18 is `packing_data`.
Consequently the first virtual slot invoked by the fragment-end handler
returns to the same RTC dispatcher for a normal v2 instance. This establishes
that assembled bytes are an inner RTC stream, not necessarily a raw video NAL.
Other subclasses/overrides are not covered by this symbol check.

No recursion-depth check was found in the inspected dispatcher/end pair.
Do not reproduce this recursive path in the local implementation. The next
boundary should inspect inner complete-record lengths iteratively with byte and
record-count limits, reject nested fragment types until a bounded policy is
explicitly supported, and publish nothing if any inner record is incomplete.
The current assembly helper intentionally returns opaque bytes only; it is
not safe to wire directly into an AV decoder. Vtable inspection used 19.4 MiB.

## Atomic inner record boundaries implemented

`push_rtc_inner.split_inner_rtc_records` now validates the entire assembled
immutable byte stream before returning a tuple of opaque records. Local limits
are 256 KiB total and 256 records. Unlike outer relay envelopes, a reassembled
inner AV record may exceed `0x8400 - 20`; applying that transport bound here
would incorrectly reject larger assembled frames.

Only 0x80/0x81/0x82/0x83 are accepted. Nested fragments and unknown types are
rejected without recursion or resynchronization. Every declared body must fit;
AV prefixes require 24 bytes, header entries require exact count/length agreement,
and user-data bodies must be nonempty. AV payload semantics are not validated by
this boundary. Trailing partial records fail the whole call; no generator or
callback can publish an earlier record before the final validation succeeds.

Synthetic tests cover mixed types, 256 KiB and 256-record boundaries, forged
lengths, nested fragments, partial tails, and fragment assembly split inside
inner headers/payloads. This remains offline, without production callers.
The inner-boundary checkpoint passed 292 focused tests with 77 MiB peak/no swap
under a 256 MiB/50%-CPU cap; Ruff and GitHub CI passed.
