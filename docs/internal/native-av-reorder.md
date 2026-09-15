# Bounded cross-channel AV ordering — 2026-09-15

The experimental paired receiver now tolerates media-channel START and subsequent
media arriving before the control-channel ACCEPT. Independent KCP sequence spaces
do not guarantee relative arrival order across channels. This is an offline
protocol milestone, not a production native stream or live interoperability claim.

## Safety contract

- Only an exact START with the expected call ID may open the pending queue.
  Media before START, wrong calls/actions and malformed envelopes remain terminal.
- Retained complete TLVs stay encrypted until correlated ACCEPT. They do not set
  peer-started, encoding readiness or active status before acceptance.
- FIFO replay preserves media-channel order. Existing KCP duplicate suppression
  prevents duplicate storage or delivery; partial V1 records survive the transition.
- `av_pending.py` caps retention at 128 messages and 256 KiB, with an absolute
  one-second deadline starting at the first queued START. New data and duplicate
  traffic do not extend it. Existing transport/parser caps remain in addition.
- Exceeding a limit closes both receive channels and clears all retained state.
  Already ACKed data is never silently dropped while keeping the session alive.
  The owner must poll during silence and close its socket on failure.
- `AvHandshake` still requires INIT receipt, ACCEPT, peer START, local START receipt
  and encoding readiness independently. This queue does not grant decoder readiness.

The single-channel compatibility mode keeps its original ordering requirements.
No worker, socket, decoder, camera action or production capability was introduced.

## Repeatable evidence

```sh
prlimit --as=536870912 --cpu=30 -- .venv/bin/python -m scripts.replay_native_av re/pcapdroid/pcap.pcap
prlimit --as=536870912 --cpu=30 -- .venv/bin/python -m scripts.replay_native_av re/pcapdroid/pcap.pcap --delay-accept
```

The second scenario holds the first complete ACCEPT datagram for at least 100 ms
of capture-relative time. Only one datagram is held by the script; no network I/O
occurs. Both ten-second prefixes returned active with 481 datagrams, one encoding
header, 151 video records and 155 audio frames, one unhandled command and zero
buffered bytes at completion. Peak combined receive buffering was 40,836 bytes
in both runs. Counts are parser evidence, not a new codec or latency measurement.

Nine new synthetic cases verify no early decryption, exact-once draining, timeout
under silence/late ACCEPT/duplicates, byte/message caps, receiver cleanup on overflow,
wrong-call rejection and handshake receipt separation after reordered input.
All 134 selected handshake/send/receive/V1/existing media-session tests passed with
a 512 MiB address-space cap. Ruff, Mypy (180 files) and the frontend toast/panel/PTZ
contracts passed. The previously
reported full-suite native crash is not resolved by this focused verification.

The bounded socket adapter is now implemented and simulated separately; see
[native-av-probe.md](native-av-probe.md). Camera-3 live reception and remote teardown
validation remain pending, with no change to the production RTSP source.
Reconnect, pacing, A/V synchronization and eventual
single-producer handoff remain separate milestones. No build/restart is needed for
this offline-only change. Capture files and credentials remain ignored/private.
