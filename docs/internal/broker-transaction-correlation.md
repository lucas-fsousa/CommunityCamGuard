# Bounded broker receipt correlation — 2026-10-11

`drivers/yoosee/p2p/broker_transactions.py` implements the first isolated unit of
the [ownership migration](native-stream-ownership-plan.md). It does not allocate
sockets, authenticate, send commands, start workers or change existing PTZ/media.

One owner handles at most eight pending transport receipts. Each ticket binds a
caller-owned authenticated generation, non-reused uint32 sequence and message
type; the owner also binds the broker session. Timeout is finite, at most ten
seconds. Registration cannot evict another waiter, even an expired one: `poll()`
returns expired tickets explicitly for the future dispatcher to notify callers.
`cancel()` withdraws exactly one matching ticket without recycling its sequence;
`close()` returns all cancelled tickets and invalidates the owner.

The accepted input is a complete decrypted 32-byte mode-2 transport ACK with
valid length/checksum, matching session/type/sequence and a live deadline. It
returns the uint16 result: zero is positive transport receipt, not application
success. Nonzero is a terminal negative receipt, never an automatic retry.
No response payload, encryption key, callback or history is retained.

Peer verification, decryption and bounded fragment reassembly remain responsibilities
of the account socket owner. Generation is local callback isolation, **not wire
authentication**: the caller must bind it to its authenticated session/key and never
label an old callback as current. A new owner with the same session/key cannot
distinguish replayed wire bytes merely by giving itself a new generation number.
Sequence exhaustion rejects further registration instead of wrapping under the
same session. Future account renewal must use fresh authenticated session state.

Offline tests cover interleaved receipts, wrong session/type/sequence/mode/generation,
corruption, negative result, duplicates, exact deadline expiry, bounded capacity,
sequence exhaustion, cancellation and close. Real mode-2 encrypted ACKs pass
through `BrokerMaintenance` and out-of-order fragment assembly before correlation;
partial/wrong-peer/wrong-session input cannot complete a transaction. A thousand
unrelated receipts neither accumulate history nor extend a pending deadline.

Remaining migration work: application-response correlation (including device/path
or embedded request identity), a single bounded account socket dispatcher, then
read-only preparation integration. Do not replace existing application parsers or
interpret this transport-only component as readiness to enable a native source.
Production entry points do not import it yet; no container rebuild is required.
