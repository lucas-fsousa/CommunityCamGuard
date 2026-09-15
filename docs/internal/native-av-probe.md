# Bounded native AV socket adapter — 2026-09-15

`p2p/av_probe.py` adds `probe_av_socket()` around the existing socket-free handshake.
It consumes one exclusively owned socket on an already authenticated, freshly
opened/metered direct route. It does not discover cameras, load credentials, reserve
a camera, initialize another AV session or register a production driver capability.
The caller must enforce exact camera identity, exclusive reservation and unused AV
sequence spaces before transferring ownership. Route preparation is outside this
function's deadline; the new outer owner is described in
[native-av-route.md](native-av-route.md).

## Resource and failure contract

- Maximum ten seconds of reception plus two seconds for CLOSE receipt, with socket operations limited to 50 ms and cancellation
  checks before sends, after reception and between iterations.
- At most 10,000 received datagrams, 8 MiB received and 2 MiB transmitted. Noise
  and foreign traffic count toward receive budgets; no unbounded drain loop.
- Receive buffer is 2,048 bytes to detect/truncate oversize MTP traffic above the
  protocol's 2,047-byte limit. Oversize packets are discarded, never parsed as a
  valid prefix. Byte accounting measures returned bytes, not truncated wire bytes.
- Only INIT, START, CLOSE and transport ACKs are sent. No audio payload, microphone,
  PTZ, siren, lighting, decoder, file writer, worker or media-output queue exists.
- Record counts are consumed per batch. Only aggregate counters are returned;
  existing bounded handshake/KCP/V1 retention remains in force.
- By default socket and handshake state are closed on success, invalid arguments, cancellation,
  protocol/budget failures and network errors. No reconnect or ambiguous-send retry.
  Network exceptions are sanitized rather than exposing endpoints.
  With `close_socket=False`, only handshake state is closed here; the outer route
  owner must release and close the socket on every exit.
- Success requires negotiation/header readiness, at least one video record and
  CLOSE transport receipt (not semantic teardown proof);
  it does not establish playable decoding, keyframe readiness or A/V synchronization.

## Validation and actual scope

Nineteen fake-socket cases cover reordered media, exact outgoing control families,
invalid durations/routes, send/receive failure, partial sends, silence, cancellation,
three traffic budgets, foreign/oversize/non-MTP input, flood limits, and cleanup of
retained protocol state. The fake socket drives the real handshake and parsers.
The selected Python regression group totals 153 tests; Ruff and backend Mypy
(180 files) also pass. Tests run serially with a 512 MiB address-space cap.

**No socket was opened against any camera in this milestone.** No container build,
service restart or production stream change occurred. Existing RTSP and recording
paths are untouched. There is no live CLI/API entry point yet.

## Before the camera-3 experiment

1. AV CLOSE ownership/receipt correlation is now implemented and simulated; see
   [native-av-close.md](native-av-close.md). B9 cleanup is now composed and tested
   in [native-av-route.md](native-av-route.md); neither receipt is physical proof.
2. Correlated channel-4 meter replies are now implemented; unsolicited keepalive
   timing remains unvalidated. See [native-av-meter-diagnostic.md](native-av-meter-diagnostic.md).
3. The reserved internal diagnostic is implemented, but a safe same-process operator
   trigger remains pending. Never borrow the production stream/intercom socket,
   run an old AV initializer first, or assume a separate process shares operation locks.
4. Perform one short, bounded camera-3 run and record actual readiness, record counts,
   teardown evidence and effects on existing RTSP. Do not infer firmware support
   or enable native streaming from simulated success.
