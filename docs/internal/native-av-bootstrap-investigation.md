# Native AV bootstrap investigation — 2026-09-16

## Observed runs

The image containing `90ca08a` phase telemetry was built with a 512 MiB / one-CPU
limit and deployed for camera 3 only. A single POST returned 502 after 4.44 seconds.
Session resumption happened later; the initial `--since 5m` log query missed that
run before app recreation. Its failed phase cannot be recovered from that output.
Do not use a short wall-clock log filter for a possibly resumed tool session.

After the user's subsequent continuation, one separately armed attempt returned
502 after 4.49 seconds. This time the entire current container log was filtered
for the fixed safe telemetry marker **before** recreation. It reported:

```text
stage=av_receive_close outcome=failed error_type=ValueError
release_attempted=True release_acknowledged=True elapsed_ms=4485
```

This confirms the fresh-route procedure reached the AV entry point and received
the correlated B9 release transport receipt. It does not confirm video reception,
AV CLOSE, decoded frames or physical camera-resource release. The diagnostic was
disabled and its configured target cleared after each invocation. No automatic
POST retry was used.

## Code-backed inference and correction

The selected duration and generated call/link/cookie satisfy the AV constructor's
constraints. Receive/parser failures would surface as `ReceiveError`, not plain
`ValueError`. The remaining AV entry validation requires both bootstrap flags:
`channel.direct_acknowledged` and `channel.meter_acknowledged`. The old route owner
did not check those flags before labeling the next stage as `av_receive_close`.
This points to incomplete media bootstrap, not a decoder or speaker problem;
which flag was missing was **not recorded in these runs**.

A bounded historical PCAP scan found two mode-1 A4 acknowledgement frames matching
the existing parser's 32-byte/action-4 shape and three 177-byte direct requests.
Thus there is no capture-backed reason to change that frame shape or remove the
ACK requirement based on the current evidence.

The route owner now rejects incomplete bootstrap in `media_meter`, before the AV
initializer. `AvBootstrapError` carries only the two confirmation booleans and
received-datagram count. The authenticated loopback endpoint returns those fields
with the fixed phase label while retaining the consumed-attempt policy. No secret,
endpoint, device ID or exception text is returned. Existing teardown still runs.

Four regression cases cover every incomplete flag combination, no premature AV
invocation, cleanup, phase labeling and the safe one-shot HTTP response. Neither
timeouts, A4 framing, acceptance requirements nor retry counts were changed.
Full Python 3.12 validation passed 1,743 tests, with one Node-dependent case skipped
in the capped container; Node contracts passed separately on the host. Ruff and
Mypy (186 files) passed. No full-suite native crash was reproduced under Python 3.12.

## Runtime state and next action

The running image has the earlier phase logs, not this new structured bootstrap
error. Diagnostic disabled, target cleared, health OK; all three base RTSP streams
had one producer and nonzero received bytes after restoration. go2rtc and unrelated
WSL containers were not restarted; the app recreations did interrupt recorder
ownership as documented for the first attempt.

Next: after green CI, deploy this change for one reviewed camera-3 invocation and
inspect direct-ACK versus meter-ACK evidence. Do not remove either validation or
increase the timeout merely to get past the guard. Continue to capture the safe
result/log and disarm in the same command sequence, before handing control back.

## Structured follow-up result (same day)

Commit `e1bbea7` passed remote CI (run `35098838102`) and was then deployed for
one separately armed camera-3 sample. The endpoint returned 502 after **3.83s**:

```json
{"phase":"media_meter","direct_acknowledged":false,"meter_acknowledged":true,"datagrams":2}
```

The retained log reported `stage=media_meter`, `error_type=AvBootstrapError`,
`release_attempted=True`, `release_acknowledged=True`, and `elapsed_ms=3821`.
This confirms incomplete bootstrap: a route-matching meter was observed, but the
existing parser did not recognize a direct A4 receipt. AV INIT was not invoked.
It does not yet distinguish absent/lost A4 replies from replies ignored by prefix,
source, decryption, length, sequence or action checks. The two datagrams were not
saved as payloads, so their individual classifications are not available.

No protocol requirement was weakened and no immediate retry followed. The standard
app configuration was restored in the same command sequence after collecting the
safe HTTP result and log. Runtime checks confirmed opt-in false, targets empty,
health OK and one producer on each base RTSP stream. This updated error reporting
is now deployed; native video remains disabled/unhomologated.

Next actual investigation: compare direct-A4 acknowledgement handling with the SDK
and known working intercom bootstrap, then classify bootstrap traffic safely if
another capture is needed. Do not diagnose an AV decoder/timing failure or bypass
the missing receipt from a meter response alone.
