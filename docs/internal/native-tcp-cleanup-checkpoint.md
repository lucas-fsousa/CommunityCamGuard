# Native TCP continuity and cleanup checkpoint — 2026-10-11

## Offline continuity

`test_vendor_mtp_tcp_continuity.py` exercises the complete measured/negotiated TCP
owner, then 100 sequential media records, each duplicated, with TCP chunks of
1, 13, 74 and 4096 bytes. Exactly one payload/timestamp per record is delivered;
buffers drain after each pair; CLOSE receives its correlated transport receipt.
A separate missing-sequence case expires after two simulated seconds, clears
buffered media and rejects late delivery on that closed owner. No real sleeps,
network, media decoding or camera actions occur in these tests.

Combined startup/transport suite: **109 tests passed**, 2.769 seconds, peak
89.9 MiB, zero swap under a 256 MiB/no-swap/50%-CPU cap. Ruff passed. These tests
prove bounded parser behavior, not network throughput or production backpressure.

## Bounded live continuity

The first continuity attempt failed in access establishment, before A4 or media;
its sanitized output recorded only `P2PProbeError`, not a precise cause. A separate
access-only check succeeded without a token refresh or media allocation. One
subsequent camera-3 attempt then reached the three-second post-readiness target:

- **38 HEVC frames, 1920×1080**, all decoded after local sockets closed.
- 33,953 temporary video bytes, zero pre-IDR discards; timestamp span 3,702,000
  raw ticks. Fifty-eight audio records counted only, not played or retained.
- Largest observed video-record arrival gap: 201.8 ms. This is application
  arrival timing (including parsing/coalescing), not network latency or FPS proof.
- 86,623 TCP bytes/158 records received, 7,092 bytes emitted; two transmission
  attempts each for INIT/START/CLOSE. Readiness and CLOSE receipt confirmed.
- Sample cleared, owner/socket closed, broker B9 receipt still unconfirmed.
  Some broker AA traffic arrived during cleanup; it is not a B9 acknowledgement.
- Combined parent/decoder peak **77.1 MiB**, zero swap, **10.683 seconds** under
  the unchanged 256 MiB/no-swap/50%-CPU/60-second cap.

This is a short continuity check, **not a sustained soak test**, long-term camera
health proof, production backpressure validation or permission to enable a native
video capability. No other camera, production restart or playback UI was involved.

## Broker hangup: verified fields, unresolved receipt

Targeted disassembly of SDK 6.45 ARM64 `libiotvideomulti.so`, SHA-256
`510bc51a5545907b53b3f1e61625a7e453d4e72d69f658a1dca75838ec0756aa`
(same build as the [quality mapping](native-video-definition.md)):

- `giot_eif_send_hungup_msg`, `0x2554f8`, 896 bytes, calls the inner initializer
  at `0x2555e0` with channel `+0x1e0` and `+0x1f0`, type zero.
- `giot_init_frm_p2p_inner_msg`, `0x25114c`, 304 bytes, sets type B9, length
  0x4c; writes its destination argument at frame +0x1c and local identity from
  context +0x2e0 at +4/+0x24. Its fourth argument is written at frame +0x38.
- Back in the caller, `0x255600–610` separately copies MTP session +0x5e8 to
  frame +0x3c. `0x255618–620` copies the supplied reason to +0x40.
- At `0x255668` it queues reliable broker delivery. A second branch, guarded by
  channel address/port +0x320/+0x324, builds a mode-1 direct copy. That does not
  authorize guessing an endpoint or adding another sender to our diagnostic.

The two link fields originate from **different runtime fields**, even if equal
on a fresh route. The current builder accepts one fresh link and duplicates it;
that assumption must not silently extend to remapped/reconnected routes. These
reads do not prove which value caused the observed missing B9 acknowledgement.
No wire format, retry count, destination, identity or production default changed.
Builder documentation now distinguishes requesting release from proving it.

The static checks used 128 MiB/no-swap/50%-CPU/40-second cgroups, peak below
38 MiB. Next trace the channel +0x1f0 assignment and actual send-time header
preparation before changing teardown. AV CLOSE receipt, B9 transport receipt,
physical socket closure and remote resource reclamation remain separate facts.

### Assignment traced: fresh route versus reused route

The next two bounded reads narrow this issue without changing the wire:

- `iv_process_calling`, `0x250284` (1604 bytes), allocates a fresh MTP session
  at `0x2502b8`, then copies MTP +0x20 to channel +0x1f0 at `0x250364–370`.
  Together with the previously mapped session constructor (initial +0x20 equals
  +0x5e8), this supports our two equal fields on a **fresh** diagnostic route.
- `iv_start_process_calling`, `0x24f74c` (1572 bytes), has a reuse branch that
  can increment bits 24–29 of +0x5e8 (`0x24fb98–bb0`) before requesting a new KCP
  session. It still copies +0x20 to channel +0x1f0 at `0x24fcc0–ccc`.

Thus distinct field origins are not evidence that the current fresh-route B9
payload is wrong. They matter when future reconnects reuse an SDK-style route.
Do not "fix" missing acknowledgements by changing these IDs without new evidence.
Next unresolved boundary is queued broker send-time header/session preparation
and acknowledgement handling, not the now-traced fresh-route assignment. Both
additional inspections peaked below 29 MiB/no swap; no camera traffic was sent.
