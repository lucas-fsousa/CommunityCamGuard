# Native media path: actual LAN selection investigation — 2026-11-06

## Evidence and scope

Only camera3 is used for current live checks. No audio, light, movement, reboot,
reset or camera configuration was sent. The other two cameras stay legacy RTSP.
Do not infer camera3 support from a historical capture of a different unit.

The July PCAP contains dominant camera→app LAN UDP media (2358538payload bytes,
3854packets), while our production native source connects to a public TCP relay.
`p2p/relay_selection.py` explicitly accepts only global IPv4 TCP candidates.
The SDK has LAN/NAT channel selection; our single-relay path is not its complete
route scheduler. This is an implementation difference, not proof that every
KCP failure is caused by the relay. Do not tune timeouts/window sizes blindly.

The numeric evidence now retained across lease retries shows a real gap at
19:04:47Z: next_sequence951065, pending89, parts0, gap1, bytes75145,
window_rejected0. Thus that sample does NOT demonstrate a full/rejecting receive
window. Both TCP physical close and broker release were confirmed; retry worked.
A later decoder progress_timeout at19:44 also recovered. The API was still
native/active168 when checked; no permanent-block recurrence was observed there.

## Pin the ELF: similar SDK labels have different addresses

Current inspection uses
`re/apk/yoosee-6.45-xapk/config.arm64_v8a.apk!lib/arm64-v8a/libiotvideomulti.so`,
SHA256`510bc51a5545907b53b3f1e61625a7e453d4e72d69f658a1dca75838ec0756aa`.
The older extracted file has SHA256`b4d6f72168b666f80b08ebdd2eb210f57b5fd8199d8b375963f2568c5e96900a`.
Earlier documents'0x202xxx addresses belong to that OTHER binary; do not transplant
them into the split ELF. Use the existing `re/p2pdis.py` with `P2PDIS_SO` explicitly
set, named functions/short address ranges,192MiB/CPU50%/30s scope. Actual peaks
were27–36MiB, with no whole-APK analysis, emulator or persistent subprocess.

| Split-ELF evidence | Meaning |
| --- | --- |
| `iv_start_av_link`0x1d86dc–0x1d86f8 | Reads matching LAN-table row: IPv4 at row+0x2e → channel+0x320; port at row+0x2c → channel+0x324. |
| `iv_process_calling`0x250450–0x250474 | LAN path requires a known address/port and no disabling channel flag. |
| Same function0x2505dc–0x250640 | Builds AF_INET endpoint, requires nonzero IPv4/port, adds LAN/NAT channel type2. |
| Same function0x2506cc–0x250704 | Queues direct CALLING and broker CALLING separately before transfer checking. |
| `get_lan_device_info`0x1d51e4; `find_dst_id_inlan`0x1d4c08 | LAN-table access and matching; numeric identifier branch uses atoll. |
| `iv_access_init`0x1d6694–0x1d6778 | Creates broadcast manager with callback0x1d68f4; starts receive/send/device-info processing. |
| `broadcast_fd_init`0x26a044–0x26a0d8 | Binds IPv4 INADDR_ANY starting at8900, increments if occupied. |
| `send_loop`0x26b2b0–0x26b3e4; `send_msg`0x26ae24 | Native discovery is not the old Java1024-byte big-endian cmd1 request. Constructs a28-byte little-endian70/02 frame, reply port+0x1a, identifier-kind low bits+0x18, mode flags0x02010000; sends to8899. |

## Read-only live checks

- Kernel routes to camera3 and relay choose the same LAN interface; ARP matches
  camera3's recorded MAC. A different chosen local interface was not demonstrated.
- Passive header-only capture during app startup (90s) saw no direct camera3 UDP.
  It ran in a disposable64MiB/CPU25% container and was removed automatically.
- The old Java unicast discovery request to camera3:8899 returned nothing.
- A native28-byte SDK request, using identifier-kind2 from the numeric-ID branch,
  returned nothing with either LAN-specific or SDK-style ANY bind at8900. A raw
  header-only check during the latter also saw no incoming camera3 UDP. The actual
  app's runtime identifier-kind was NOT captured; do not call this interoperability
  validation or infer that LAN is unsupported. No broadcast to other cameras ran.
- Windows read-only Hyper-V firewall inspection identifies the WSL creator policy:
  Enabled, DefaultInboundAction=Block, DefaultOutboundAction=Allow. Specific WSL
  UDP allow rules found were only mDNS5353; no native discovery8900 allowance.
  Host-policy merging is enabled; this alone does not prove which packet was
  dropped. A temporary rule restricted to camera3→UDP8900 requires owner approval
  and must be removed after repeating the same query. Never disable the firewall
  or set a blanket inbound Allow as a troubleshooting shortcut.

Also fixed: AvReceiver sanitizes a nested KCP failure to ReceiveError. The shared
worker must unwrap that local cause chain like the original singleton path;
otherwise the known transient code/counters disappear again. The existing
five-source integration now reproduces the sanitized wrapper, not only a directly
raised KCP exception. This is recovery consistency, not a new protocol guess.

## Broker-provided LAN candidate: do not block on discovery

The historical PCAP has215 native70/02 requests and no captured70/03 reply, yet
LAN media exists. All215 request headers use flags0x02010000, identifier-kind
word10 and reply port8900, with zero terminal ID. Thus the native query used in
the read-only live probe matches those captured fields; discovery silence alone
does not establish that a broker-assisted direct route is impossible.

The decisive split-ELF branch is `gat_on_rcvpkt_MTP_RES_RESPONSE`:

- 0x247800–0x24781c: outer IPv4 at A3+0x60, NETWORK-order port at+0x58.
- 0x247ad0–0x247b28: if peerlanip at+0x64 is nonzero and differs from the
  outer IPv4, construct AF_INET using HOST-order port at+0x5a and add LAN/NAT
  kind2 with `iv_mtp_session_add_lan_or_nat`. This branch does not depend on a
  broadcast discovery response.
- +0x5c / +0x68 are the IPv6 port/address, not the LAN IPv4 tuple.

The account's existing authenticated broker handler now preserves the correlated
private LAN tuple separately from the TCP relay. Unit coverage verifies the
different port byte order and rejects wrong session/link/checksum and unsafe or
duplicate addresses. This is passive extraction ONLY: production still uses its
existing TCP channel. No UDP channel, punch, extra login or camera action is
silently enabled by the parser. Next implementation must integrate the existing
socket-free UDP AV engine into the SAME account reader and prove the candidate
on camera3, not start a competing reader/login or invent a fixed camera port.

At58.419s the saved PCAP already sends a LAN MTP meter request; a broker A3 arrives
at58.470s, followed by public NAT traversal at58.486s. This proves concurrent
route attempts, not that that particular A3 supplied the earlier LAN address.

## Cooperative measurement on the actual account reader

The existing leased worker now measures the authenticated A3 LAN candidate with
at most two c0/90 meters, 250ms apart, and a1.5s result deadline. TCP starts and
continues normally; there is no blocking receive, extra socket/login/thread,
AV INIT on UDP, camera setting or media-route replacement. Local send errors or
unavailable ordinary output credit cannot terminate the TCP source. Cancelling
the route stops measurement, and completed/detached routes retain no probe history.

The same account recvfrom feeds direct meter replies before broker processing.
Roundtrip requires exact peer, link, source/destination, checksum, reply shape,
channel4, and the sequence/full timestamp of a measurement actually attempted.
The split SDK0x258c34-0x258c50 maps LAN channel kind2 to meter channel4;
reusing the existing channel4 builder is therefore SDK-backed, not a guessed
protocol variant. Unknown packets and requests cannot establish success.

Only fixed outcomes (no_candidate/no_roundtrip/roundtrip) and attempt counts
are logged, using an explicitly enabled narrow INFO logger. No endpoints,
identifiers, tokens, cookies or payloads are logged. Offline coverage includes
the actual leased account worker with TCP progress and broker release on the
same socket. This is route measurement, NOT production UDP video acceptance.
