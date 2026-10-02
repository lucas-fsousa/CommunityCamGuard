# Push reply evidence: capture triage and the E3 distinction

2026-10-01. Continues [push reception](yoosee-push-reception.md); SDK identity is
recorded in [platform provenance](yoosee-platform-sdk-versions.md).

## Existing capture, without collecting new traffic

`scripts/inspect_push_capture.py` reuses the bounded streaming RAW IPv4 PCAP reader
in `scripts/pcap_input.py`. It inspects UDP datagram starts and TCP segment starts
for protocol 3, known push types, and consistent declared lengths. It never prints
addresses, ports, identifiers, timestamps or payloads. It does not decrypt, scan
arbitrary byte offsets, reassemble IP fragments or reassemble TCP streams.

The existing ignored capture (`re/pcapdroid/pcap.pcap`, 4,443,195 bytes) yielded:

| Measurement | Count |
| --- | --- |
| Records | 12,436 |
| UDP packets / payload-bearing packets | 10,623 / 10,623 |
| TCP packets / payload-bearing packets | 1,813 / 871 |
| Candidate push headers at inspected boundaries | 0 |

This is **not proof that the capture lacks relay traffic**. Header splits,
non-boundary frames, encryption and unsupported encapsulation are outside this
inspection. It supplies no authenticated `03/07` response fixture or teardown
receipt. Even a future positive classifier result would be a candidate, not proof
of authenticity or successful certification.

Reproduce locally with `python -m scripts.inspect_push_capture PATH`, under the
same resource caps used for RE. Eight synthetic tests cover bounds, fragments,
UDP exact lengths, TCP partial/coalesced prefixes and content-free output.
The real scan peaked at 12.3 MiB with no swap under a 128 MiB/30-second/50%-CPU cap.

### Follow-up: bounded reconstruction of captured TCP prefixes

A second offline inspection grouped payload-bearing IPv4 TCP segments by directional
address/port tuple, ordered them by sequence number (accounting for SYN), validated
identical retransmission overlaps and assembled only continuous captured data.
Limits were 8 MiB retained payload total, 128 directions, 4096 segments per direction
and 256 KiB assembled per direction, under the same 128 MiB/no-swap/30-second cap.
No files or content were exported; memory peaked at 7.3 MiB.

All **60 payload-bearing directions** assembled continuously within those limits;
none produced a known protocol-3 candidate at its **captured stream prefix**.
This eliminates initial TCP segmentation as the explanation for the first-pass
result for these prefixes. It still does not inspect arbitrary later offsets,
decrypt TLS/proprietary encryption, identify camera sessions or prove the capture
starts at application-stream boundaries. Numeric sequence sorting is not a general
wraparound/reused-four-tuple TCP implementation. This follow-up is an evidence
audit, not a production reassembly transport or a missing-feature conclusion.

## E3 is not a relay certification response

`gat_send_push_stream_rsp` (`0x27cdc0`, 624 bytes, SDK 6.45) builds a **GAT** frame
starting `7f e3`, with declared size `0x90`, terminal identity at `+4`, operator at
`+0x1a`, relay cluster at `+0x1b`, relay properties/address at `+0x1c..+0x23`,
encryption metadata at `+0x24..+0x2f`, and a 96-byte copied context at `+0x30`.
It queues the frame through `iv_gutes_add_send_pkt` at `0x27cff4`.
It does not build the relay protocol-3 type-7 certification response.

`iv_pre_snd_push_stream_rsp` (`0x27cd18`, 168 bytes) chooses a preferred relay node
with `iv_get_pre_rly_node`, writes its cluster into session `+0x23`, then invokes
that E3 builder. A bounded direct B/BL xref search found no caller of this wrapper;
indirect/exported invocation is not excluded. Reachability must not be assumed.

The E3 queue callback `iv_gutes_on_Ackfrm_push_stream_rsp` (`0x27cba8`, 192 bytes)
logs timeout when its callback status argument w3 is 2 and logs success otherwise.
The strings say “end push stream frame”, but this function does not validate a
relay hangup receipt or a certification body. Do not equate that log with remote
resource release, and do not add a shutdown sender based on the string alone.

## Next evidence needed

- Capture-prefix reconstruction is complete for the 60 observed directions;
  application-layer decoding may still recover additional evidence. Neither
  negative result may become a capability flag or justify repeated live probes.
- Trace any independently evidenced response schema and its peer/session binding
  before declaring certification success.
- Keep broker E3 acknowledgement, broker B9 release and relay `03/0b` hangup as
  separate concepts. Remote lifetime before certification remains unproven.

No network sockets, camera actions, container rebuilds or raw capture exports were
performed. The diagnostic is offline tooling, not an enabled driver transport.
