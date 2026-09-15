# Bounded AV CLOSE ownership — 2026-09-15

The experimental handshake/probe now performs AV CLOSE after successful reception.
The action is the existing `build_av_control(call_id, 7)` used by the legacy
intercom lifecycle; no new action value was guessed. This milestone was tested
with fake sockets only, without contacting a camera or rebuilding containers.

## Sequence and receipt contract

`AvHandshake.begin_finish()` requires full negotiation/header readiness. This
exclusive owner has emitted exactly one base-channel START at sequence zero, so
CLOSE owns sequence one on that same conversation. INIT's high-bit conversation
and all incoming sequence numbers/UNA remain independent. Arbitrary sequences,
sequence-zero CLOSE, and nonzero INIT/START are rejected by the sender.

The existing sender retains one CLOSE wire, permits four byte-identical attempts
250 ms apart, and expires after two seconds. The pinned peer, conversation,
sequence, echoed timestamp, empty ACK body and zero fragment counter must match an
actually issued request. START receipts, cumulative UNA, unsent or late ACKs cannot
confirm CLOSE. Repeated `begin_finish()` does not allocate a new request or extend
its deadline. No subsequent START is emitted during closing.

The same bounded receiver/parser remains alive during closing: ACKed partial media
is not discarded or reconstructed. Records are still consumed. Protocol failure
or timeout closes all state. `ready` becomes false while finishing;
`close_acknowledged` means **transport receipt, not semantic teardown confirmation**.

## Socket probe integration

After up to ten seconds of negotiated video reception, the probe allows up to two
additional seconds for CLOSE receipt. Packet/byte budgets are shared across both
phases, not reset. Successful results report historical negotiation readiness and
the separate CLOSE receipt. Missing CLOSE receipt is an error, not silent success.
Cancellation, malformed input, budget exhaustion or socket failure still closes
locally immediately; no new CLOSE exchange is initiated after a failed/cancelled
session. All exits release the socket and protocol state.

## Evidence and remaining live work

Fifteen new synthetic cases cover sequence ownership, retained partial media,
idempotent bounded retry, receipt mismatches, unsent and late receipts, premature
CLOSE rejection, invalid sequence combinations, missing CLOSE response and
cancellation during closing. The probe's existing success case now asserts exact
outbound INIT/START/CLOSE actions and sequences, with no audio or other controls.
All 168 selected tests passed, alongside Ruff and Mypy (180 backend source files),
using serial, memory-capped Python checks. This is not full-suite approval or live
camera interoperability evidence.

Before live camera-3 validation, review meter responses and wire the exact-camera
reservation plus fresh authenticated route preparation. The route owner must also
use the existing distinct brokered B9 hangup (`rendezvous_session.py`) in cleanup:
AV CLOSE receipt and local socket closure do **not** replace P2P route release.
Do not borrow the production RTSP/intercom socket or grant native-video capability
based on these simulated tests.
