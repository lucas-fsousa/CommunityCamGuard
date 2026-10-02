# Platform provenance differs between SDK builds — 2026-10-01

The E4-only conclusion applies to SDK 6.45, not every SDK in the RE workspace.
The older binary used for native-video research also has an MTP promotion path.
Neither finding identifies camera 3 without a device-correlated observation.
No runtime parser, capability, quality selection or camera session was changed.

The initial investigation above was documentation-only. The follow-up below
corrects the passive parser; it does not enable a new transport or camera action.

## Clear E4 bit is not a platform-1 observation

Reinspection of SDK 6.45 `0x24dfcc–0x24dff4` confirms that a clear option bit
skips the registry setter entirely. A positive bit invokes the promotion setter;
the negative branch does not reset the device to platform 1. The prior parser's
`2 if bit else 1` therefore overstated the evidence and could downgrade a
previously positive observation during rendezvous/media setup.

`DevicePlatformMetadata.version` is now optional: a structurally valid,
device-correlated E4 without the bit yields `None`, while a set bit yields 2.
Collectors retain their previously observed version on unknown metadata.
Envelope parsing still succeeds for unknown platform, so independent push-context
extraction is preserved. These checks establish neither authentication nor a
functional relay session; callers retain responsibility for trusted transport and
session provenance. There is no new platform-1 inference or HD activation.

49 focused offline tests passed, including clear-only, positive-then-clear,
clear-then-positive and preservation of rendezvous evidence during media setup.
Existing push-context tests cover clear-bit envelopes. Peak test memory was
59.6 MiB, no swap, under a 256 MiB/50%-CPU cgroup; Ruff passed.
The separate [getter audit](native-video-definition.md#gettercache-audit--2026-10-01)
also rules out the local player quality getter as an independent query of the
camera's applied resolution.

## Binary identity

| ARM64 `libiotvideomulti.so` source | SHA-256 | Registry setter |
| --- | --- | --- |
| Existing extracted native-AV research binary | `b4d6f72168b666f80b08ebdd2eb210f57b5fd8199d8b375963f2568c5e96900a` | `0x2265cc` |
| 6.45 ARM64 split APK, `lib/arm64-v8a/libiotvideomulti.so` | `510bc51a5545907b53b3f1e61625a7e453d4e72d69f658a1dca75838ec0756aa` | `0x277524` |

## Older SDK: positive MTP evidence

Reverse B/BL references, including PLT stubs, to
`giot_eif_get_set_device_version` identify these call sites:

- Public getter: `0x197394`, with operation `w2=0`.
- Session creation: `0x20b42c`.
- `iv_rcv_meter_req`: `0x20dba0`, setter operation `w2=1`.
- `iv_rcv_meter_ack`: `0x20e9b4`, setter operation `w2=1`.

At `0x20db5c–0x20dba0` and `0x20e970–0x20e9b4`, the receive handlers
test bit 5 of the word at meter-body offset `+8` (our codec's `flags`). When set,
they store platform 2 at session `+0xa4e` and update the session device's registry.
The setter at `0x2266a8–0x2266b0` writes 1 to registry entry `+0x290`;
its getter maps a nonzero field to platform 2 (`0x226680–0x2266a0`). This is the
same registry used by quality selection, not merely an unrelated transport flag.
An absent bit does **not** prove platform 1.

This evidence is specific to the binary above. Future observations must preserve
device/session provenance and the existing peer/checksum/route/sent-meter checks.
Uncorrelated historical flags must not silently enable a camera capability.

Historical capture follow-up: 671 meter candidates yielded 669 parsed messages,
none with positive bit `0x20`. This does not establish platform 1 and supplies no
positive candidate to correlate to camera 3. The content-free diagnostic and limits
are recorded in [capture evidence](yoosee-push-capture-evidence.md).

## SDK 6.45: E4 participates in push/relay setup

The same reverse-reference scan finds `gat_rcv_PushStreamDistribute` at
`0x24dfec`, the getter at `0x1dc08c`, and session creation at `0x25a890`,
not the older MTP setter call sites. This resolves the apparent contradiction
with the previous 6.45 SD notes.

`gat_rcv_PushStreamDistribute` starts at `0x24d5fc`, size 2,956 bytes:

- `0x24d990–0x24d9c8`: reads frame `+0x28` as MTP link/session ID, first
  checks an existing push session using `+0x2c`, delegating if found.
- `0x24d9cc–0x24da1c`: otherwise requires an MTP-associated channel; exits if absent.
- `0x24dcb0–0x24dd08`: creates `iv_push_session_app_new` and changes channel state.
- `0x24dd18–0x24de04`: parses relay counts after the variable token. This branch
  rejects no relay and an IPv4 relay count other than one.
- `0x24df64–0x24dfc8`: adds a TCP relay; exits on failure.
- `0x24dfcc–0x24dfec`: frame `+0x18` bit 0 promotes device `+0x38` in the registry.
- `0x24dff4` onward: selects a push channel and starts certification/detection timers.

**Inference:** direct LAN/MTP negotiation need not produce this push-distribution
message. Repeating the same LAN probe is not justified as an E4 recovery strategy.
The full upstream triggering exchange remains to be traced; receiver-side evidence
does not establish a safe request recipe.

## Reproduction and next step

Offline sequential inspection used 256 MiB/no-swap/50%-CPU cgroups, 40-second
runtime ceilings, and roughly 26–36 MiB observed peaks. No emulator, browser,
APK-wide decompile, camera command or container restart was used.
Ignored `re/p2pdis.py` and `re/disasm_symbols.py` now support `archive!member`
with a 16 MiB ELF-member ceiling, avoiding extraction/replacement of the old SDK.
Those local tools are not included in this documentation commit. Standard ELF
tools can reproduce the symbol/address findings from the pinned binaries.

Next: trace the SDK option/caller initiating push distribution, or establish a
device-correlated positive MTP observation under the older SDK contract. Keep HD
operator selection gated until provenance is resolved. Never infer platform 1
from silence, overwrite both quality fields, or substitute inventory flags.

Related: [quality encoding](native-video-definition.md),
[first native decode](native-av-first-live-decode.md).

## A4 advertisement traced — 2026-10-01 follow-up

Same 6.45 binary and resource limits. `iv_init_frm_CALLING` (`0x24fd70`,
1,184 bytes) unconditionally ORs `0x4000` into the 16-bit options at A4 `+0x18`
at `0x24ff44–0x24ff50`. Its log string at `0x2501b8–0x2501bc` names bit 14
`opt_support_psrelay`. Combined with its other unconditional option writes,
the base options are `0x4581` (before optional user-ID and direct-path changes).
This is a support advertisement, not proof of a command forcing E4 delivery.

`iv_process_calling` constructs that A4 at `0x2503ac–0x2503b8`. For a known
LAN endpoint it clones the packet at `0x25048c–0x2504e0`, then alters the original
for direct mode at `0x25066c–0x25069c` (header direct flag, mode 1, option bit 1).
The clone is submitted separately at `0x2506e0–0x250704`; the non-LAN branch
submits the original broker packet at `0x250794–0x2507b8`. The advertisement
therefore originates before splitting direct and broker request paths.

Current standalone codec comparison (no runtime changes):

| Path | A4 options | Push-relay bit |
| --- | --- | --- |
| Default broker request | `0x0581` | absent |
| Broker with explicit live/SD connection metadata | `0x4581` | present |
| Default direct request | `0x4483` | present |

The existing passive-only platform probe uses default broker metadata. Its failure
to observe E4 is consequently not equivalent to the full SDK's broker request.
This is a concrete request difference and a plausible explanation, **not proof**
of the broker's policy or a guaranteed way to solicit E4. The original SD mapping
had already identified the bit; this follow-up closes its general CALLING builder
and direct/broker copy chain and compares the live probe's default path.

Do not enable the bit globally or manufacture HD/SD userdata just to obtain it:
the E4 handler can replace MTP with a push/relay session whose lifecycle is not
implemented in our runtime. Socket-free regression tests pin current default,
explicit-live and explicit-SD options without contacting any device. Next useful
work is a separately gated observation design with push-session cleanup accounted
for, or exact-device historical MTP provenance under the older SDK contract.

The next teardown investigation recovered a separate reason-zero push hangup
frame and checksum; its socket-free encoder remains unconnected to runtime.
See [push teardown and remaining lifecycle gaps](yoosee-push-teardown.md).
