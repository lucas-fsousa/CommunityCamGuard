# V1 microphone channel correction — 2026-11-15

Physical retest on the prior build again produced silence:500/500 audio frames
and OFF transport receipt in10.680seconds, but owner explicitly confirmed no
speaker sound. Concurrent HD delivered23binary messages/8399766bytes, max gap
0.836seconds, no observer error; all sources remained native/active333/63/60.
No acoustic acceptance or new driver capability follows from those receipts.

## Narrow SDK evidence

Saved ARM64 `libiotvideomulti.so`, SHA256
`b4d6f72168b666f80b08ebdd2eb210f57b5fd8199d8b375963f2568c5e96900a`,
BuildID `b1de33db0f4e0c23e8a9d5ce1dccf6820160b991`.
Only selected function windows/dynamic relocations were inspected; no APK-wide
decompile or SDK execution. Disassembler tasks capped96MiB/no swap/25%CPU,
measured23–26MiB peak.

`StreamPipe::Impl::send_data` at0x136fdc first packs the protocol-specific record.
At0x137034 it reads the `Unpacked` DataType. Ordinary user data (type3) directly
takes `iv_send_user_data_link_chn`. Crucially, the special sequenced-user-data
type4 shortcut at0x1371ac only executes **after** a successful `dynamic_cast` to
`trans_proto_v2` (0x13714c–0x13716c).

The actual RTTI relocations are unambiguous:

- GOT0x2400f8 -> `typeinfo for iotvideo::trans_protocol`.
- GOT0x240100 -> `typeinfo for iotvideo::trans_proto_v2`.

V1 does not pass that cast. Its packed type4 sequenced command falls through to
`iv_send_data_link_chn` at0x1371d4, **the same path as encoded media**:

`iv_send_data_link_chn` (0x1943e0)
-> `giot_eif_send_data` (0x207400)
-> `iv_mtps_snd_avdata` (0x20fdb4)
-> AV ring at channel offset0x880.

`iv_send_av_ringbuf` (0x211018) builds outer TLV type4/subtype2 at
0x21129c–0x2112f8, encrypts complete8-byte payload blocks from offset4 with RC5
and sends AV data. It is not the separate command TLV type2 path used in the
previous transmitter.

`iv_send_kcp_data` at0x210e58 selects KCP context offset0x5d8 for type4 at
0x210f54–0x210f78, versus offset0x5e0 for type2 at0x210f84–0x210fa8. This
confirms the separate AV/command conversation choice, not just an envelope name.

## Resulting correction and limits

For the currently supported **V1** AV owner, microphone ON/OFF must use the base
media KCP conversation and `encrypt_media_tlv`, retaining the existing37-byte
v1 sequenced-user-data record. Header, microphone ON, audio, microphone OFF and
eventual AV CLOSE share the monotonically allocated base sequence space.
Codec, audio descriptor, timestamps, pacing and volume are unchanged.
The inbound V1 parser also accepts bounded0x0300 sequenced records on the AV
ring, preserves them as non-media data and counts them without interpreting
command execution. A valid interleaved reply must not kill incoming video.

This supersedes the earlier high-channel claim in `re/notes/two-way-audio.md` and
the original cooperative transport tests. Those tests encoded an incorrect
assumption; their passing ACK contracts were never speaker evidence. The V2
branch must not be copied onto this V1 receiver. Standalone historical intercom
probes remain unhomologated and are not selected by the current driver.

This is a concrete static protocol discrepancy, not yet proof that the corrected
speaker output works. A fresh authorized physical reference test is required;
no production audio proof or dashboard activation is made by this correction.

## Corrected-channel physical retest

The test on `ae5f0f7` completed in11.496seconds with500/500 audio frames and
microphone-OFF transport receipt. The concurrent HD observer received26binary
messages/9395054bytes, maximum gap1.040seconds, no observer error. All three
sources remained available in native mode (revisions336/66/63).

The owner explicitly confirmed **no sound** in this corrected-channel attempt
as well. The channel correction therefore does not establish a working native
speaker path. Recorded-message and hold-to-speak shared-native capability proofs
remain ungranted; do not substitute transport receipts for acoustic acceptance.

Following that feedback, a fresh read-only canonical speaker-volume GET returned
HTTP200, `value=50`, `verified=true`. The speaker setting was50%, not100%; this
check neither changed volume nor sent audio. Audible behavior at100% has not
been tested in this attempt.

### Speaker reference at100%

On the owner's request to continue correcting the issue, the canonical control
PUT changed camera3 from50% to100%, with `verified=true`, `changed=true`, error0.
The existing normal message endpoint then sent the same ten-second reference PCM
through the established non-shared audio path: HTTP200, `completed=true`, elapsed
10.77seconds. The owner confirmed “agora eu ouvi”. This accepts the speaker
reference, not shared-native talk. No shared-native capability was granted.

The subsequent isolated native comparison at100% also produced **no sound**,
confirmed by the owner. It completed transport in10.628seconds (500frames/OFF);
HD delivered24binary messages,8126385bytes, maximum gap0.769seconds, no error.
All sources remained native and available (revisions339/69/66). Raising volume
is not a demonstrated fix for the native path.

The next diagnostic preserves bounded microphone command metadata from both V1
AV sequenced records and control-channel compact/sequenced wrappers. Only0x32
messages of8–16bytes are retained; unknown wrappers stay unhandled. The talk
owner logs only correlated command stage, origin, flags, payload size and an
optional exact eight-byte scalar result. No raw packet, cookie, identifier,
audio or command timestamp is logged. These observations neither grant a
capability nor mean a request was executed; the existing transport-only outcome
is unchanged. This closes a receive-observability gap, not the acoustic failure.
