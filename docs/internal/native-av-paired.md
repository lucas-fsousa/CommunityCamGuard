# Paired AV receive conversations — 2026-09-15

## Historical correlation

A bounded scan of complete controls on the same endpoint pair and base link found:

| Flow | Conversation high bit | Control | Relative arrival | Call ID matches A4 |
|---|---|---|---|---|
| 3 | 1 | INIT (1), four occurrences | 0/52/121/220 ms | yes |
| 4 | 1 | ACCEPT (2) | 276 ms | yes |
| 5 | 0 | START (6) | 276 ms, after ACCEPT in capture order | yes |
| 6 | 0 | START (6), opposite direction | 281 ms | yes |

No endpoint, cookie, call ID or media payload is included here. This establishes
the observed direction-bit/ordering relationship, not general firmware support or
cryptographic authentication. Capture timestamps are relative, not camera UTC.

## Implemented

The experimental `AvReceiver` now accepts an explicit optional `control_conv`.
Paired mode requires a nonzero 24-bit base media conversation and its exact
high-bit control counterpart. Each has its own bounded KCP receiver, sequence
space, ACK/UNA and fragment assembly. Control inactivity alone is not media
inactivity; a missing control fragment still has the original two-second deadline.

Activation requires matching ACCEPT on control, matching START on media and a
complete encoding header. START before ACCEPT fails closed, including reordered
cross-channel arrivals; bounded cross-channel reorder handling is **not implemented**.
Media cannot masquerade as control or reuse its acceptance sequence number.
Failure in either channel clears both plus the V1 parser. Caller-owned sockets
still must be closed by the caller. No INIT or outgoing START is sent here.

After acceptance, well-framed command TLVs (type 2, flags 1/2) on the control
conversation are counted as `unhandled_commands`, not decoded, queued or treated
as command success. They do not activate the session or refresh media progress.
Unknown AV controls and malformed envelopes remain terminal. Retained KCP payload
is capped at 256 KiB per conversation (512 KiB combined), plus the existing bounded
V1 parser. No threads, workers, decoder or background process are created.

## Repeatable offline result

```sh
prlimit --as=536870912 --cpu=30 -- .venv/bin/python -m scripts.replay_native_av re/pcapdroid/pcap.pcap
```

The script streams the ignored PCAP, resolves at most 16 flow labels and selects
flow4's correlated route. It replays at most ten seconds with capture-relative
time and polls session deadlines. A4 binding changes fail closed. Output contains
only counts, phase and buffered bytes. ACK bytes are built but **not sent**.

Observed result: active; 481 inbound datagrams; one encoding header; 151 complete
video records; 155 audio frames; one unhandled command; zero pending bytes at the
end of this prefix. This is a parser/receive lifecycle validation, **not a new
independent codec decode**, complete-capture audit, live handshake or latency test.
The existing historical codec decoding milestone remains separate.

Focused tests cover the paired sequence-zero spaces, fragment reordering, wrong
channel/action/call, START without ACCEPT, media without START, quiet control while
media progresses, shared failure cleanup and non-authorizing command receipts.
The full Python suite's previous exit-139 failure is unresolved; avoid claiming
full regression approval on the basis of focused tests.

Validation for this change: all 87 tests in the seven receive/parser test files
passed under a 512 MiB address-space cap. Ruff, Mypy (173 files including the replay
script) and the separate frontend camera-panel/PTZ contracts also passed. The
full suite was not repeated, and no memory cap was increased.

## Next

The socket-free, one-control reliable outbound primitive is now implemented and
tested: [native-av-control-send.md](native-av-control-send.md). Composition into a
live INIT/ACCEPT/START owner is still pending; transport ACK is not AV acceptance.

Implement reliable outbound INIT/START ownership and correlate ACKs to actual sent
sequences, preserving intercom's validated behavior. Decide bounded handling of
cross-channel reorder before live deployment. Then perform a short camera-3-only
receive experiment. No production caller, dashboard capability, stream replacement
or container rebuild was introduced by this milestone.
