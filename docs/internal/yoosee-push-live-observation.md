# Camera-3 push advertisement observation — 2026-10-09

Scope: reviewed test-camera enrollment, native Python client, no vendor-app
gateway, AV START, relay certification, property write, sound, light, movement or
reboot. Production containers were not restarted. The diagnostic remains in
ignored `re/probe_camera3_push_advertisement.py`; it reads existing enrollment
without printing credentials or addresses.

## Request and correlation

Broker A4 uses the existing live INIT's exact 32-byte userdata and connection
type 1, advertising `0x4000` through the existing codec (options `0x4581`).
No HD/SD metadata is invented and production defaults remain unchanged. Each
attempt has fresh link/call identifiers. A3 establishes the expected peer; its
`7f/ca` must match the current link before the diagnostic answers NAT online.
Broker frames must decrypt and match the current session identity. E4 acceptance
also requires mode 2 and exact device/link correlation. No relay is contacted.

## Results and limits

- A4/A3 and a correlated direct handshake succeeded without the vendor app.
- Initial observations discarded `70/01` broker datagrams. The diagnostic now
  reuses the checksummed fragment codec, two-group assembly limit and `70/02`
  receipt ACKs for the expected broker only. Outer fragment identities are not
  assumed to equal the inner session identity.
- The confirming run received seven fragments and assembled three mode-2 `AA`
  messages matching the broker session. They were **not E4**. Missing fragment
  handling is real in the observation path, but is not established as the cause
  of absent push distribution. No global receiver change follows from this result.
- The run observed 80 packets / 31,187 bytes before cleanup, completed in 9.46 s,
  peaked at 38.2 MiB and used no swap. A preceding run received no fragments.
- No correlated E4 or relay certification was obtained. Platform remains unknown;
  a direct handshake does not establish platform 1, platform 2 or HD.
- B9 teardown was attempted once and local sockets closed in `finally`, but a
  correlated remote receipt was not observed. Remote release is **unconfirmed**.

Caps: 192 MiB, no swap, 50% CPU, 30-second hard runtime; receive/transmit budgets
remain enforced by `BudgetSocket`. No payloads, tokens or identifiers are retained
in tracked documentation. No production code was changed on this evidence alone.

## SDK follow-up / next step

Targeted inspection of pinned 6.45 ARM64 `iv_on_ackfrm_Calling` (`0x2508c8`,
736 bytes) shows an optional public-endpoint response at offsets `0x20/0x24`
and a session port update at `0x250b54–0x250b74`. This callback does not itself
send a new push solicitation. Inspection peaked at 29 MiB, no swap, under a
128 MiB cap; no APK-wide decompile.

Next: compare SDK calling/channel transitions and relay selection with our direct
route, including teardown correlation. Do not repeat unchanged A4 probes, force
undocumented flags, or require vendor-app capture instead of native investigation.

### Separate MTP relay path found

A bounded reverse-reference scan found a separate relay path:
`gat_on_rcvpkt_MTP_RES_RESPONSE` calls `iv_mtp_session_add_tcp_relay`
at `0x24816c` and `iv_mtp_session_add_udp_relay` at `0x2482cc`.
The UDP branch checks the supplied address and avoids an existing UDP node before
adding one (`0x248278–0x2482cc`). The UDP helper at `0x25b2ac` copies an
endpoint into a new channel and links it into the MTP session; it is not the
E4 push-session certification path. A preceding branch adds a LAN candidate
through `iv_mtp_session_add_lan_or_nat` at `0x247ec0`.

Our current `parse_mtp_peer_endpoint` returns only one public IPv4 endpoint from
A3 and does not enumerate such alternatives. This gives a concrete independent
branch to investigate rather than assuming all proprietary relay traffic needs
E4. **It does not yet prove the received camera-3 reply contains usable relays.**
Next inspect the handler's decode call and bounds before implementing candidate
parsing; intermediate SDK structure offsets must not be copied as wire offsets.
No alternate endpoint was contacted. These inspections peaked below 29 MiB.

### Passive MTP table codec and live envelope

`mtp_relays.py` is a separate, socket-free parser; it does not reuse the E4
descriptor flags. The SDK handler takes the receive frame at argument `x1+0x1b0`
(`0x247708–0x247710`), resolves the MTP session using frame `+0x1c`, and reads
v4/v6 counts at `+0x78/+0x79` (`0x247f3c–0x247f88`). Tables start at `+0x7a`,
with strides 16/28. The SDK clamps counts to 32/16; our parser rejects overflow.
Descriptor flags are little-endian at entry `+8`. The **UDP view** of ports at
`+10` is network-order: the UDP helper copies them into `sockaddr` and converts them for logging
(`0x25b350–0x25b35c`, `0x25b398–0x25b3a4`). Addresses begin at `+12`.

The selection loop tests bit 2 of **either paired family descriptor** before
choosing the TCP branch (`0x2480a4–0x2480d4`). The parser therefore retains raw
flags and independent family indices, without pretending a single entry proves
UDP/TCP readiness. Zero ports and special addresses remain data, not permission
to connect. It requires mode-2, matching session/link, no ACK/compression and exact
known-layout length. Production callers and capabilities are unchanged.

Two bounded camera-3 observations rejected the table layout. The second recorded
a correlated uncompressed A3 with counts **4 IPv4 / 0 IPv6** and 196 bytes, while the mapped
table ends at 186. The extra ten bytes are **not mapped yet**: the strict parser
correctly rejected this layout rather than silently accepting an extension.
This is a concrete live compatibility gap, not evidence of four usable relays.
No candidate address was printed or contacted. The runs peaked at 39.1/38.3 MiB,
no swap; neither started AV nor obtained E4 or confirmed B9 receipt.

Next: identify those ten bytes (padding, extension or alternate layout) from the
SDK/capture before relaxing validation. Do not repeat the unchanged probe merely
to confirm the same counts, and do not treat failure as camera incompatibility.

### Variable suffix and explicit diagnostic inspection

Follow-up observations produced 214- and 194-byte A3 envelopes: their suffixes
were 28 and 8 bytes, respectively. The prior ten bytes are therefore not a fixed
padding rule. A single 194-byte decrypted sample was saved with mode `0600` in
ignored RE storage for offline reuse. Its GAT XOR checksum verifies. Offline
inspection found four IPv4 entries, four nonzero ports and flags `0x7` for each;
the suffix remains opaque. No endpoints or payloads were printed or committed.
The two observation processes peaked at 57.9/38.3 MiB, without swap.

`inspect_mtp_relay_advertisement` now returns the bounded known table **and an
explicit, repr-hidden opaque extension**. Its 64-byte suffix budget is local
diagnostic policy, not an inferred firmware limit. The existing strict
`parse_mtp_relays` still rejects extensions. Neither API opens sockets or enables
capabilities. This separates useful table evidence from an unsupported claim of
complete envelope interpretation; the extension cannot supply extra endpoints.

The SDK suffix branch at `0x24830c–0x2483c4` reads `unlock_utc` only when
frame option bit 2 is set, after an optional four-byte field selected by bit 1.
The saved frame's option word is `0x6500`, so those branches do not explain its
suffix. Do not label it padding, an authentication token or a timestamp.

### TCP pairing request, separate from E4 certification

In SDK 6.45, `iv_mtp_session_add_tcp_relay` schedules TCP via
`ivtcp_comm_add_connect` at `0x25c6c0` (IPv4 path). Independently inspected
`iv_on_tcp_connect_finished` constructs a 74-byte frame at `0x25b900–0x25ba98`:

| Offset | Content |
| --- | --- |
| 0..5 | `c0/80` MTP prefix, encoded total length 74, existing rotating-XOR checksum |
| 6..9 | zero, request kind 1, little-endian record length 68 |
| 10..13 | u32 from MTP session `+0x5e8` |
| 18..25 / 26..33 | u64 source / destination from session `+0x30/+0x38` |
| 38..45 | full u64 monotonic millisecond tick |
| remaining body | zero-initialized |

`mtp_tcp_handshake.py` encodes this socket-free request with strict unsigned
integer widths. It does not reuse the direct `c0/90` meter's extra fields or the
E4 relay certification token. **Session `+0x5e8` assignment is still unproven**;
do not substitute the calling link at `+0x20`. The response/readiness transition
and callback registration chain were still pending at that codec checkpoint.
No pairing packet was sent at that checkpoint. Targeted disassembly stayed below
128 MiB/no swap. The subsequent limited live experiment is recorded below.

### Native MTP TCP transport reached; media still unproven

Further SDK inspection closed the initial identifier assignment:
`iv_mtp_session_new` seeds the low 24 bits at session `+0x5e8`, clears bits 24–29
and copies the value to calling link `+0x20` at `0x25a908–0x25a914`.
This establishes equality on creation, not on every reused-session transition.

The first TCP attempt was refused before any application packet was sent.
Review found a concrete parser/diagnostic error: we had generalized UDP port
byte order to TCP. TCP's helper **reverses** the word before storing it in its
sockaddr (`0x25c110–0x25c124`, IPv6 `0x25c164–0x25c178`).
`ivtcp_comm_add_connect` copies that sockaddr unchanged at `0x1edf80–0x1edfb4`,
and `ivtcp_start_connect` passes it to `bufferevent_socket_connect`.
The TCP view is therefore little-endian on the advertised bytes, unlike the UDP
view. Descriptors now expose explicit `udp_port` and `tcp_port`, not one ambiguous
port. Both family views have regression coverage. This corrects the earlier
generic-port statement without changing the unrelated E4 codec.

After that correction, one fresh camera-3 route's first advertised global IPv4
TCP candidate accepted the connection, received the exact 74-byte pairing request
and returned **82 bytes starting `c0/d0`, with a valid MTP checksum**. A second
instrumented observation reproduced the result. Each attempt used one advertised
candidate, one send, a three-second TCP budget, at most 4096 received bytes, no
address/port scanning and no AV INIT/START or camera-control command. TCP sockets
were closed in `finally`. Both runs peaked at 38.4 MiB with no swap.

This is real native TCP relay transport, not a successful media session:
the response does not match the existing plain `c0/90` meter layout. Applying
that layout's offsets yields no link/device/timestamp correlation. Do not relabel
it a meter ACK or successful certification merely because its checksum is valid.
The TCP receive callback at `0x25bd00` accepts MTP prefix bit 4, handles partial
records and dispatches to `iv_on_mtp_tcp_frm`; that dispatch/decode path is next.
No E4 or confirmed B9 receipt was observed. Production remains unchanged.

The experimental connector lives only in ignored `re/mtp_tcp_probe_once.py` and
is opt-in through `CCG_PROBE_MTP_TCP=1`. It accepts current broker-session/link
correlation plus checksum, permits only advertised global IPv4 TCP candidates,
and does not reinterpret the opaque A3 suffix. The first refused attempt's
top-level `relay_contacted` flag was incorrectly left false; its nested
`tcp_probe.attempted` was true. The diagnostic bookkeeping is corrected.

### Camera-correlated MTP request received through TCP relay

`iv_on_mtp_tcp_frm` routes prefix bit 7 to `iv_on_rcv_mtpCtrl_pkt` at
`0x25b554`. That dispatcher (`0x253884`) checks prefix bits 5–6 and selects a
body offset of 6 when zero, or **14** otherwise (`0x2538a8–0x2538dc`). It
looks up the link at body `+4`, then dispatches kind 1 to `iv_rcv_meter_req`
and kind 2 to `iv_rcv_meter_ack`. The observed `c0/d0` has the extended layout;
the earlier plain-offset comparisons were inapplicable, not an authentication
failure or evidence of an unrelated camera.

One instrumented repeat received the same 82-byte/checksum-valid frame. At the
SDK-defined offset its body has **kind 1, length 68, the current attempt's link,
camera 3 as source and our access ID as destination**. Its timestamp does not
echo our request, consistent with a new request rather than an ACK. This proves
a camera-correlated MTP control request reached our native client through the
advertised TCP relay. It does not prove cryptographic peer authentication, AV
readiness, HD support, E4 delivery or remote B9 teardown. No ACK or AV packet was
sent in response; the TCP socket closed after observation. Peak 38.4 MiB/no swap.

`parse_mtp_tcp_meter` now separately models the observed extended layout with
exact length, checksum and three-way link/source/destination correlation; kind
1 remains distinct from kind 2. Socket-free tests cover truncation, corruption,
wrong identities and the full 64-bit timestamp. It does not widen the direct
`c0/90` parser or strip extension bytes before verifying the original checksum.
Next: trace the extended request ACK builder and ownership/lifetime, then test
the bounded exchange. Keep AV START disabled until that exchange is confirmed.

## TCP request ACKs — 2026-10-10

Targeted inspection of `iv_rcv_meter_req` (`0x25cc7c`, 2508 bytes) recovered the
actual reply, rather than assuming an echoed request or reusing the UDP builder:

- Extended body selection at `0x25cd38–0x25cd60` initializes the outgoing eight-byte
  route prefix with the request **source ID**, not the received opaque prefix.
- `0x25cfbc–0x25d06c` builds kind 2, body length 68, preserves the link, swaps
  source/destination, copies sequence and the complete 64-bit timestamp, copies
  channel/record length (minimum 68), and sets body byte 64 to 2. Other body
  fields are zero-initialized, not blindly echoed.
- TCP input mode 2 selects outgoing `c0/e0`, total body length plus 14 and a fresh
  checksum at `0x25d554–0x25d604`.
- Plain TCP input selects `c0/80`, or `c0/90` only for channel types 1/2, and
  length plus 6 at `0x25d424–0x25d548`. It is not the generic UDP ACK branch.

`mtp_tcp_ack.py` implements these two fixed-layout builders, separately from
the pairing/parser module. Both require checksum-valid, current link/source/
destination and request kind 1. Oversized records, the unimplemented body
extension flag, wrong routes, incoming ACKs and truncation fail before sending.
The pure functions open no sockets. Neither a valid ACK nor a successful local
send proves remote acceptance or session readiness.

The opt-in ignored diagnostic now receives bounded split/coalesced TCP records
and sends at most **one extended ACK and one plain ACK**, on the same connected
socket. Limits remain one advertised global IPv4 candidate, three seconds,
4096 received bytes and six records, under 192 MiB/no-swap/50%-CPU/30-second
process caps. No AV INIT/START, KCP payload or camera action was sent.

The final camera-3 test sent both ACKs and received six checksum-valid,
link/source/destination-correlated **kind-1 requests**: three `c0/d0` records
of 82 bytes and three `c0/90` records of 74 bytes (468 bytes total). No kind-2
reply echoing our initial pairing timestamp was observed. Earlier intermediate
observations with only the extended ACK likewise must not be described as a
roundtrip. TCP closed locally; broker B9 remote receipt remains unconfirmed.
Peak live process memory was 38.6 MiB/no swap in 8.861 s; the first ACK probe
peaked at 43 MiB. Production containers were not rebuilt or restarted.

216 focused codec/parser tests passed in 1.08 s at 80.1 MiB/no swap; Ruff passed.
Next: trace the SDK's post-pairing outgoing meter scheduler/builder and the
ACK-driven channel readiness transition. The initial relay pairing request is
not necessarily the camera-directed measurement whose timestamp can be echoed.
Do not repeat the same pairing/ACK test hoping to obtain a different result.

The subsequent static inspection found a distinct periodic measurement builder:
`iv_mtp_chnnel_send_meter_frm` (`0x258758`, 1452 bytes) writes a sequence from
channel `+0x130`, flags `0x08`, a 72-byte record (68 plus four-byte call data),
full timestamp, session source/destination, role and channel-dependent type before
calling `iv_mtp_chnnel_send_mtp_frm` at `0x258cc0`. The latter is a separate
2860-byte transport wrapper at `0x257c2c`, not the initial 74-byte pairing packet.
This distinguishes the next measurement to trace from the already-tested pairing
request. Its relay envelope/selection must be verified before sending it.

## Correlated periodic TCP roundtrip — 2026-10-10

The distinct measurement is now mapped and **one live camera-3 roundtrip was
observed**, without the vendor app, AV startup or any physical camera command.

`iv_mtp_session_add_tcp_relay` creates native channels 0x86/0x85 at
`0x25c4bc`/`0x25c59c`. For these channels the periodic builder leaves body channel
zero, writes base length 68 but record length 72, flags 8, sequence, full 64-bit
timestamp, session role and the four-byte call ID. `iv_mtp_chnnel_send_mtp_frm`
at `0x258470–0x2585d0` wraps channel 0x86 as `c0/e0` with the destination ID
as its eight-byte route prefix (86 bytes total); 0x85 uses `c0/80` (78 bytes).
The UDP MTU padding branch does not apply to these TCP channels.

`mtp_tcp_measurement.py` encodes these socket-free requests separately from
pairing and ACK builders. Its narrow response matcher requires the observed
86-byte `c0/d0`, kind 2, base length 68, record length 72, flags zero and exact
link/source/destination/sequence/full-timestamp correlation. Important: the
legacy MTP checksum covers only the first 24 payload bytes, **not the whole
record**. Neither checksum nor correlation constitutes cryptographic peer
authentication or proof of SDK channel readiness.

The bounded ignored diagnostic sent one extended ACK, one plain ACK and one
periodic measurement on the same socket. Among six received records (472 bytes),
the fourth was the 86-byte kind-2 response: matching current route, sequence and
full timestamp, flags zero and record length 72. The other records were the
previously observed kind-1 requests. This proves a response to our camera-directed
measurement, rather than merely observing unsolicited requests. No repeated
probe was needed. Process peak: 42.7 MiB, no swap, 8.893 seconds. TCP closed in
`finally`; broker B9 remote receipt is still unconfirmed. Production unchanged.

266 focused tests passed in 1.41 seconds (81.1 MiB/no swap); Ruff passed. Tests
cover exact SDK layouts, both transport types, unsigned-width rejection, ACK
correlation, truncation, partial checksum coverage and unsupported envelopes.
Next: map the ACK-driven SDK channel selection/readiness and teardown ownership
before enabling AV INIT/START. Do not equate this roundtrip with working media,
LAN-only operation, E4 reception or confirmed broker release.

Related: [SDK provenance](yoosee-platform-sdk-versions.md),
[push lifecycle](yoosee-push-teardown.md),
[existing native decode](native-av-first-live-decode.md).
