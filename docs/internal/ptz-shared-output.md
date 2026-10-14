# Shared PTZ output reservation and RELEASE observations

2026-10-14. Source-tested foundation; no motion admission in the shared worker,
no deployment and no camera traffic.

## Output accounting

`AccountOutputBudget` now supplies the finite read worker's existing limits:
512 attempted datagrams and1MiB total output. Attempts are debited before transport
I/O and are never refunded after ambiguous failure. The worker still admits only
identity reads; it does not yet create a PTZ reservation.

The budget can hold one typed `PtzOutputReserve`: one START, three RELEASE attempts,
and two receipt sends, each with up to4096 bytes reserved. Ordinary reads/heartbeats/
ACKs cannot borrow the reserved packet or byte capacity. Category limits prevent
consuming STOP capacity as another START. Unused per-packet byte slack becomes
available after charging the actual attempted size; remaining full slots stay
protected. Closing the reservation releases only unused capacity, not spent output.

This is accounting, not a dispatch queue, motion scheduler or authorization layer.
The sole owner must combine it with the typed prebuilt commands and only relinquish
cleanup capacity after resolving the motion lifecycle. A stale reservation cannot
debit its replacement. Two receipt slots are finite, not permission for unlimited
ACK traffic; exhaustion handling remains part of future dispatcher integration.

## Correlated RELEASE delivery

`ReleaseDelivery` accepts already authenticated/decrypted frames from the sole
reader, bound to the exact peer/session/generation/access/device and RELEASE
sequence/message/request identifiers. Its observation window is at most2 seconds
and64 frames, counting unrelated input. Exhaustion fails closed.

A correlated application success, or both transport and peer receipts, establish
delivery. An explicit correlated application error is sticky and defeats prior
success; a subsequent success cannot erase it. Wrong identities, stale generations,
malformed frames and replies outside the original deadline cannot confirm delivery.
Frames arriving after the deadline are not observed and do not revise the result.

Returned parsed replies still need bounded receipt handling by the future sender.
No socket read, acknowledgment send, timer, automatic retry or motor-state proof
exists here. The future owner must finish the observation/cleanup policy rather
than immediately retire on the first positive reply.

## Verification and remaining integration

107 focused budget/reservation/worker/preparation/PTZ-codec tests passed under a
192MiB/no-swap/half-CPU cgroup, peak86.4MiB. Ruff and targeted mypy passed. Tests
cover ordinary-output floods, packet/byte exhaustion, stale reservations, ambiguous
attempt accounting, response-before-receipt, explicit-error precedence and identity/
deadline/input-budget rejection. All traffic is synthetic; production PTZ unchanged.

Next: connect typed motion admission, reserved output and correlated results to a
single-owner scheduler with finite gesture/STOP deadlines and shutdown draining.
Do not enable shared-worker motion by merely widening the read mailbox allowlist.
Only after that composition is tested should the camera3-only physical check be
considered, retaining RTSP/ONVIF and current PTZ as unchanged production paths.
