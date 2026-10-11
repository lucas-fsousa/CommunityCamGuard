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

## Send-time mapping and delivery-kind correction

Further SDK 6.45 evidence:

- The relocation at GOT `0x2a8528` resolves to `gat_on_ackfrm_msg`, not a packet
  builder. That callback (`0x23da38`, 140 bytes) logs delivery timeout for its
  status argument 2; it does not implement semantic camera-release confirmation.
- `giot_eif_send_hungup_msg` supplies queue-policy words `[1, 40, 0, 3]` at
  `0x255624–640`. In `iv_gutes_add_send_pkt` (`0x1e7c50`, 2184 bytes), the **first**
  policy word becomes header bits 18–19 at `0x1e7db8–dcc`. Our builder incorrectly
  used delivery kind 3. It now uses **1**; session, reason and link fields remain
  unchanged. This is a source-backed format correction, not proof of the timeout's cause.
- The queue fills sequence at `0x1e7d8c–db0`, clears ACK at `0x1e7dd0–ddc`,
  and can replace prefix/identity with 7e/current broker session at
  `0x1e7fc0–8004` before checksum/encryption. That supports our existing broker
  session header; no change to identity or encryption was justified.
- `iv_gutes_pkt_send_ack` (`0x1e8654`, 984 bytes) builds a 32-byte ACK,
  echoes type/sequence, sets bit 20 and writes the result at +0x1a. Our minimum
  response length is consistent; do not relax it to accept truncated receipts.
- `iv_gutes_on_rcvfrm_ack` (`0x1e9ec4`, 3088 bytes) handles nonzero result codes
  (including certification/signature errors) separately, then correlates queued
  sequence at `0x1ea738–74c`. Kind 1 releases its queued sender; kinds 2/3 retain
  response-wait state. A transport ACK must not be reported as semantic release.

One camera-3 validation after the kind correction decoded seven HEVC 1920×1080
frames (28,356 RAM bytes), confirmed AV CLOSE transport receipt and cleared local
owners/sockets/sample. **B9 receipt was still absent.** No cleanup response was
observed, so no nonzero ACK status was diagnosed. Peak 74.7 MiB/no swap, 9.432s
under the unchanged 256 MiB/no-swap/50%-CPU cap. No repeated live attempt followed.

73 focused tests passed (84.2 MiB/no swap), including nine new end-to-end encrypted
receipt cases using real encryption/decryption with wrong peer/session/sequence/
type/mode/key/checksum/truncation. A valid receipt passes and invalid ones cannot
trigger a resend or claim success. This rules out those synthetic parser paths,
not packet loss, broker session lifetime or unexamined ACK result semantics.

Next: instrument broker servicing during the TCP receive interval (currently a
synchronous diagnostic), and separate correlated ACK status from positive release
reporting. Preserve no-retry cleanup and fresh-route ownership; do not refresh
credentials or relax peer/session checks on speculation. Production was not rebuilt.

### Negative receipt hardening

The next offline correction makes `close_device_route` require the full 32-byte
ACK and a zero result at +0x1a before reporting positive transport receipt.
Previously an otherwise correlated negative ACK, including `NEED_CERTIFY` (1)
or `SIGNATURE_ERROR` (4), could be reported as success. Unknown nonzero values
also fail closed. Both legacy and strict modes reject negative results; strict
mode continues to require the same peer/session/sequence/mode. Missing/invalid
receipts never trigger another teardown send or automatic credential refresh.

80 focused tests passed in 2.411 seconds, peak 83.6 MiB/no swap; Ruff passed.
New encrypted negative-result and header-only regressions cover the change.
This corrects a false-positive path; it does **not** explain the real experiment,
where no B9 response was observed. No second live test was needed for this guard.

## Concurrent broker servicing

`broker_maintenance.py` now offers a socket-free, ACK-only owner for this
experiment. It pins the broker address, requires the decrypted inner mode-2
session, reassembles at most two fragment groups and never dispatches an action,
refreshes credentials, opens a connection or retains complete messages. Outer
fragment group identity is deliberately not treated as broker session identity.
Limits: 15 seconds, 512 datagrams, 1 MiB cumulative input, 4096-byte datagrams.
Close clears fragment storage and its node/key reference.

The ignored runner opts in with `CCG_SERVICE_BROKER=1` and services at most four
ready UDP datagrams per TCP iteration on the existing socket. No extra thread,
socket or persistent media producer is created. Deadline/byte budgets still apply.

One camera-3 HD acquisition with this servicing handled **7 UDP datagrams,
3 valid complete broker messages and 10 ACKs** (including fragment receipts),
3,271 maintenance bytes. It decoded **7 HEVC 1920×1080 frames**, 29,534 temporary
video bytes; AV CLOSE receipt confirmed and all local owners/sockets/sample
cleared. **B9 remained unconfirmed**, with no observed cleanup response. This
does not support treating synchronous UDP starvation as the sole explanation.
Peak combined memory 118.5 MiB/no swap, 9.895 seconds; production unchanged.

34 focused maintenance/catalogue/correlation tests passed, peak 102.6 MiB/no swap;
Ruff and normal-import Mypy passed (105.5 MiB/no swap). These tests include wrong
peer/session, damaged/plaintext traffic, ACK-loop prevention, fragmented inner
correlation, expiry, count and size limits. Next inspect SDK hangup reason/callers;
do not repeat the same serviced acquisition without additional evidence.

## Teardown order and shared-session isolation finding

SDK `iv_stop_av_link` (`0x1d7120`, 1020 bytes) calls hangup at `0x1d7408`
with **0x4e22** (`0x1d7404`), then sets channel close intent at `0x1d7414`
and waits at most 50 × 10 ms for local channel state. Our reason is therefore
supported; do not substitute another reason experimentally.

Two distinct, bounded camera-3 observations followed (no repeated action):

- Release before physical TCP closure, after AV CLOSE receipt: 10 decoded HEVC
  1080p frames, 30,237 RAM bytes, broker maintenance 3 messages/10 ACKs; B9 absent.
  73.4 MiB/no swap, 6.212 seconds. Release attempted once before sending, so an
  ambiguous failure cannot trigger a second release in outer cleanup.
- Same ordering plus one read-only heartbeat before B9, using separate reserved
  sequence numbers: 11 decoded 1080p frames, 30,069 RAM bytes; **heartbeat also
  timed out** and B9 remained absent. 73.5 MiB/no swap, 7.536 seconds. No token
  renewal. All samples and local sockets were cleared/closed in both observations.

The last result shifts attention from B9 alone to broker-session availability.
Code review found that the standalone runner does not share the server's
`own_control_channel` RLock. `ControlSessions` prepares/maintains shared-account
PTZ sessions in the server, while `run_with_fresh_access` intentionally holds
that lock because another login can invalidate an existing route. Bounded logs
also contained `PtzKeepaliveError` events, but do not prove causality or exact
temporal attribution. The known shared-account race makes standalone results
unsuitable to establish reliable cleanup behavior, even though video decode was real.

The existing **in-server** native AV diagnostic also held only its camera lock,
not the shared broker lock. It now holds both through preparation, capture,
cleanup and optional decode; broker contention returns sanitized HTTP 409 and
consumes the diagnostic attempt without retry. Threaded offline regressions
verify exclusion, release on failure and no route allocation when busy.

The ignored runner now refuses execution unless session isolation is explicitly
attested (`CCG_ISOLATED_BROKER_SESSION=1`). This flag is not isolation itself and
must not be enabled while the existing server uses the same account. No further
standalone camera tests are authorized by this checkpoint. Use an in-server TCP
diagnostic under the existing lock after its adapter is integrated/deployed, or
a genuinely separate reviewed account; do not stop other cameras/containers or
disable their warmup to manufacture isolation. Production has not been rebuilt.

Note: after moving cleanup earlier, the ignored runner's top-level UDP counts
reflect the reset cleanup phase (sometimes zero), not whole-run input. Nested
TCP/maintenance counts remain valid. Preserve phase labels in future summaries.

## Extracted finite TCP receiver — offline integration preparation

`mtp_tcp_receive.receive_paired_av` replaces the reusable part of the ignored
runner with a typed socket-boundary primitive: supplied paired socket, existing
`TcpAvSession`, optional bounded sample, cancellation and broker-service callbacks.
It does not discover, authenticate, select a camera or expose an endpoint. The
caller must hold both reservations and retain physical socket/broker cleanup.

The receiver consumes records immediately, observes three seconds after readiness
within eight seconds total, and returns only after a CLOSE transport receipt.
It closes the protocol owner on every exit and clears a supplied sample on any
failure. It never recreates the owner or retries an ambiguous socket write.
Silence still polls protocol deadlines; broker service cannot extend the overall
deadline or hide cancellation. Existing byte/record limits remain in the owner.

Synthetic socket tests use the real measurement/AV/framing state machine with
partial reads, not a replacement protocol parser. They cover success, peer
mismatch, failed writes, EOF, missing CLOSE receipt, cancellation, broker failure
and expiry during broker servicing. Ruff and normal-import Mypy passed (40.7 MiB
type-check peak). This is offline preparation, not a new live validation or
production capability. Still needed: in-server rendezvous/TCP route owner, guarded
operator trigger, deployment, isolated cleanup validation and longer reception.

## In-server TCP route integration — 2026-10-11, offline only

`tcp_route.probe_tcp_route` now owns fresh rendezvous, one reviewed advertised
global IPv4 TCP endpoint, pairing, bounded reception, B9 cleanup and both sockets.
It uses the recovered HD startup metadata, never retries ambiguous writes and
does not renew credentials. Broker inspection receives complete decrypted frames
only after current-session validation, including out-of-order fragment assembly.
No payload is retained by the inspection hook.

`run_reviewed_native_av(tcp_relay=True)` executes under the same camera and shared
broker reservations as UDP diagnostics. Optional decode checks tools before
network activity and wipes the sample on all exits. TCP media/CLOSE success and
`broker_release` are separate facts; false release is not hidden by decode success.
Socket errors are sanitized to HTTP 502 and consume the single-use attempt.

Operator configuration, all server-side: existing `NATIVE_AV_DIAGNOSTIC_ENABLED`,
reviewed camera/device IDs and optional `NATIVE_AV_DIAGNOSTIC_DECODE_VIDEO`, plus
`NATIVE_AV_DIAGNOSTIC_TCP_RELAY=true`. Leave `NATIVE_AV_DIAGNOSTIC_PLATFORM_ONLY`
false. The endpoint remains direct-loopback-only, authenticated, absent from
OpenAPI, accepts no body/query fields, and is single-use per process. TCP defaults
false; no production capabilities, frontend source or automatic worker changed.

81 focused tests passed under MemoryMax=256M, MemorySwapMax=0 and CPUQuota=50%
(104.1 MiB peak). They cover real encrypted and fragmented discovery, negative
correlation, ambiguous A4, connect/pair/receive/physical-close failures, one cleanup
attempt, both locks, decode cleanup and HTTP mode/error restrictions. Ruff and
normal-import Mypy passed. No live traffic, rebuild or container restart occurred.

Next validation requires deploying this code inside the actual server process;
a second worker/CLI would defeat the process-local reservation. Coordinate the
shared-server restart before enabling it. Review only camera 3; trigger once,
record sanitized reception/decoder counts and B9 receipt separately, then disable
the diagnostic. Do not clear the one-shot guard, stop other cameras, run concurrent
account probes or treat absent B9 receipt as successful broker cleanup. If the
receipt is still missing, preserve evidence and inspect broker traffic/sequence
handling before another live attempt. Sustained streaming remains a later task.
## Latest live checkpoint — 2026-10-11: in-server HD and broker receipt

After operator approval, rebuilt with the legacy builder constrained to 512 MiB,
no additional swap and one CPU. Recreated only `ccg-app`; go2rtc and unrelated
containers were not restarted. A preliminary HTTP call was rejected with 403
before the diagnostic: urllib's default form Content-Type without Origin hit the
existing CSRF guard. Corrected the local client to send application/json and the
explicit direct-loopback Origin; no server guard was weakened.

One actual camera-3 attempt in the updated server returned HTTP 200:

- TCP ready and AV CLOSE transport receipt: true.
- 55 video records, 57 audio records counted (not played), one encoding header.
- 380,520 received TCP bytes, 17,808 sent TCP bytes; five broker messages serviced.
- Strict HEVC decode: **55 frames, 1920×1080**, 310,808 input bytes,
  3,549,000 relative timestamp ticks, zero pre-IDR discards.
- **Correlated positive B9 broker receipt: true.**

This closes the missing-receipt observation for this one bounded, isolated attempt.
It supports the shared-account concurrency hypothesis but does not prove the
exclusive cause of previous failures, physical reclamation of all remote resources,
sustained reliability, maximum sensor resolution, LAN-only access or browser playback.

Finally restored all four diagnostic flags false and target IDs empty; health 200.
Three current-hour recorder directories grew over ten seconds by 524,288, 786,432
and 786,432 bytes. Four app recreations occurred across rejected-client and actual
attempt/restoration; recordings were briefly interrupted, not gap-free. Client
cgroup peak was 56.6 MiB without swap for the successful cycle (server not included).
After restoration app used 112.6 MiB, go2rtc 46.23 MiB; both retain 1 GiB limits.

Next: bounded timing/stall measurements and sustained-reception preparation,
followed by generic single-producer/fan-out integration. Do not expose native TCP
as a production capability based only on this short test. Historical unresolved
receipt notes below remain evidence of earlier, confounded standalone attempts.

