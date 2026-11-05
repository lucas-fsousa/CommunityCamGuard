# TCP scheduler budget is not a transport failure

The shared account pump had a200ms per-turn wall-clock budget. A read returning
after that budget, or valid media decryption consuming the remaining time before
its ACK batch, raised a terminal transport exception and closed the entire TCP
route. This conflated host scheduling/CPU contention with a broken connection.
One live snapshot had load≈10 on2available CPUs; unrelated projects were not
stopped or modified.

The pump now yields at that cooperative limit. At most one4096-byte read can wait
for the next turn. One output batch is separately bounded to256immutable messages/
32KiB (including ACKs); no further read is allowed until that batch drains. This
is bounded continuation, not another reader, account, thread or media-history
queue. Valid records can be handed off before deferred ACK transmission.

Only a write that has **not been attempted** can wait for the next turn. Once
`sendall` raises (including a possible partial write), the batch and connection
close; those bytes are never retried on that socket. Real handshake/receive/idle/
assembly and CLOSE deadlines remain absolute. The20ms syscall timeout remains;
expired actual protocol deadlines still terminate the owner. A fixed once-per-
connection log identifies a scheduler yield without payloads or peer identities.

Deterministic tests insert a250ms delay into a read and into media processing.
Both old-code regressions fail by closing the route; the correction retains the
exact read bytes, drains ACKs in order and completes the original AVCLOSE with
one physical socket closure. Separate tests check bounded batches, partial/unknown
write refusal and unchanged absolute receive deadline.235related tests passed
under192MiB/no swap/50%CPU, peak103.6MiB. Ruff and Mypy404 passed.

This does not establish that every live failure was scheduling-related. Real
deployment/continuous observation is required before claiming stable delivery.
