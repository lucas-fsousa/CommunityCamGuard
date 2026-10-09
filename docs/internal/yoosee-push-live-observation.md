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

Related: [SDK provenance](yoosee-platform-sdk-versions.md),
[push lifecycle](yoosee-push-teardown.md),
[existing native decode](native-av-first-live-decode.md).
