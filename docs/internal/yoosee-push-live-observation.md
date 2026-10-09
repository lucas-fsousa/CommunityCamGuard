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

Related: [SDK provenance](yoosee-platform-sdk-versions.md),
[push lifecycle](yoosee-push-teardown.md),
[existing native decode](native-av-first-live-decode.md).
