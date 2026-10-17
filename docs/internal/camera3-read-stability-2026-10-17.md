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

## Stopped-route authentication generation

The production stopped PTZ cache now records a process-local broker authentication
generation. `open_camera_session` advances it immediately before node authentication,
including attempts whose initialization later fails (not failed list discovery).
A shared stopped route from an older generation is closed before liveness checking
or target selection; normal bounded preparation creates a fresh route. Returning an
older lease cannot repopulate the cache. Healthy sessions still reuse without periodic
login. No token refresh, motion replay, new polling worker or timing increase added.

The generation is conservative and driver-wide, matching broker ownership. It does
not observe other processes, external vendor clients or all experimental SDK paths.
It prevents local stale-cache reuse; it does not prove that every observed timeout
was caused by broker replacement.64 focused tests passed (106.7 MiB/no swap), covering
both warm/acquire paths, delayed lease return, failed initialization, and existing
healthy long-lived cache reuse. Live post-deployment comparison remains required.
