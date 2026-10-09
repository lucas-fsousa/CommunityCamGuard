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
Descriptor flags are little-endian at entry `+8`. Ports at `+10` are network-order:
the UDP helper copies them into `sockaddr` and converts them for logging
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

Related: [SDK provenance](yoosee-platform-sdk-versions.md),
[push lifecycle](yoosee-push-teardown.md),
[existing native decode](native-av-first-live-decode.md).
