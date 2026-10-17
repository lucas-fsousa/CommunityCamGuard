# Camera-3 read-only control stability, 2026-10-17

Runtime: commit00274b2, image95fccb553540. The probe used the authenticated canonical
HTTP API, not a second native broker client. Only camera3 was targeted. Two finite
rounds, each reading speaker volume, protection master, weekly schedule and floodlight
twice, sequentially with two-second gaps. No PUT, movement, audio, light or guard change.
Operator script: ignored `re/check_camera3_control_stability.py`, 90-second internal
budget, eight requests maximum, no automatic retry. Process cap192 MiB/no swap/50% CPU.

## Results

- First round: 6/8 HTTP200 with verified state. First two responses were409 during
  capability refresh; later reads passed at1909–2585ms. Total27.34s, peak73.4 MiB.
- Second round, capabilities already valid: 8/8 HTTP200/verified,1887–3761ms.
  Total34.28s, peak49.7 MiB. Both runs used zero process swap.
- Initial bounded log review (preceding five minutes) showed no matching maintenance
  warnings. During the probes, PTZ preparation logged broker_keepalive and
  identity_read_failed failures for the shared-account cameras, with30s backoff.
  No movement was requested on any camera, and no explicit calls targeted cameras1/2.

This demonstrates successful short sequential reads once evidence is available, not
long-duration reliability, concurrent-user acceptance or successful physical actuation.
Initial409 means evidence was unavailable, not unsupported hardware. Warnings are
temporally associated with the probes; their cause remains unproven.

## Next investigation

All production operations now reserve the broker lock, but stopped PTZ routes remain
cached across subsequent independently authenticated control sessions. Investigate
whether those sessions invalidate the cached broker route/account state; serialization
alone does not guarantee that an idle route survives a later login. Compare a passive
no-query window before changing lifetime/backoff or adding authentication retries.
Do not replay movement/actions, renew tokens on generic timeout, or weaken target,
sequence/session checks to make this test pass.
