# One-socket read/heartbeat validation — 2026-10-12

`broker_account_check.run_owned_account_check` owns one **supplied authenticated**
socket. It sends a validated batch of at most three allowlisted reads, waits for
one scheduled/correlated A0/A1 heartbeat, then repeats the reads on the same account
generation. It neither authenticates again nor borrows the PTZ cache socket.

## Bounds and failure handling

- Sixty seconds after ownership transfer; no retries, workers or media rendezvous.
- At most three read completions per phase and one heartbeat completion retained.
  Raw values remain internal and are stripped by the server diagnostic summary.
- Existing encrypted broker input checks, 4-KiB datagrams, 512-datagram/1-MiB caps,
  two fragment groups and ACK handling. `BrokerMaintenance.account_check` is a fixed
  60-second factory; default native AV limits remain 15/28 seconds.
- Polling during silence, one heartbeat slot reserved from read admission, and
  cancellation checks before writes and after reads. Responses remain visible if
  subsequently sending their ACK fails; that failure is reported separately.
- Every exit closes the account, fragment state and physical socket, including
  invalid input, cancellation, I/O failures and interruptions. A close failure
  cannot turn an otherwise uncertain result into success.

The fake-socket tests use the real encrypted codecs and a fake clock to cross the
50-second initial heartbeat interval without sleeping. They cover sequential
read/heartbeat identities, reliable response ACKs, silence, heartbeat timeout,
read failures, cancellation, interruption and physical close. This validates
software composition, not real relay persistence.

## Opt-in server integration

The existing hidden `POST /api/internal/diagnostics/broker-reads` accepts the new
server-only `BROKER_READ_DIAGNOSTIC_HEARTBEAT=true` (default false). No request field
can enable it. All primary-session, direct-loopback, reviewed-target and one-use
guards remain. Set the three existing diagnostic enable/target fields as usual;
only camera3 is authorized for this research.

Authentication retains its 20-second facade budget, account-lock acquisition can
wait up to15 seconds, and the socket experiment has a separate60-second ceiling.
Operator HTTP timeout must exceed95 seconds; timeout is not permission to retry.
The camera/account reservations span the entire check and cleanup, so other native
controls may report busy during this finite observation. This is intentionally
**not** a production solution for concurrent controls or permanent session ownership.

The safe response preserves `observations` for the initial reads and adds
`after_observations` plus heartbeat status/interval hint. Both read phases must have
three application responses with zero errors and the heartbeat must have status
`response`, with no separate failure. Transport ACKs or HTTP200 alone do not meet
that criterion. An absent transport ACK does not invalidate a correlated application
response, but must remain distinguishable in the observations.

## Live camera-3 result

One in-server run of source `7ec875a`, image `d97c56a48809`, passed: all three
initial identity reads and all three post-heartbeat reads returned correlated
application responses with `transport_result=0` and `error_code=0`. The heartbeat
returned `status=response`, `interval_hint=50`; `failure=null`, 30 sent/27 received
datagrams. Extra broker traffic/ACKs are included in those counts; they are not a
count of camera commands. There was one initial authentication, no recertification,
and the scheduled heartbeat crossed the 50-second initial delay on the same socket.

The operator process ran for64.540 seconds including app recreation/restoration,
peaked at117.2 MiB and used no swap. The Docker build was capped at384 MiB/no extra
swap/halfCPU with a4.862-MB context. These are bounded-client/build figures, not a
measurement of the server's peak. During the observation app/go2rtc used about
142/37 MiB. No audio/light/movement/media-start command was part of this check.

After restoration, all three recording totals grew over ten seconds (sorted byte
deltas:524288,1048576,1310720). App health was200, running, OOM=false, restart count0;
app/go2rtc used118.1/36.84MiB. App-only recreation briefly interrupted recording;
this evidence does not claim gap-free deployment. CI passed for source `7ec875a`
and documentation `bda29f3` (runs38296833402 and38297281077).

The finally path restored both read/heartbeat enables false, target fields empty,
and app health200. Only app was recreated for arming/disarming; recording was
briefly interrupted at those boundaries, not claimed gap-free. Unrelated containers
and go2rtc were not restarted. The experiment remains disabled after validation.

This validates finite read/heartbeat/read continuity, not simultaneous native media,
unlimited token life, device-feature support or LAN-only access. Next bounded
concurrent submission/completion delivery and explicit account handoff before any
production control migration. Do not use a standalone shared-account probe.
