# Bounded CI test diagnostics

2026-10-14: recent CI runs remain `in_progress` at pytest. The GitHub CLI does not
provide their logs before completion, so the blocking test/root cause is not yet
known. Last inspected green run38332763396 completed its quality job in63seconds.
Do not describe the pending runs as green or the unknown hang as fixed.

The quality job now has a20-minute ceiling, and pytest a10-minute step ceiling.
`pytest -v -o faulthandler_timeout=60` prints individual test names and dumps Python
thread stacks if a test exceeds60seconds. The dump is diagnostic; the Actions
timeout is what terminates the stuck step. No timeout plugin is required.

These settings apply to newly started runs, not already running jobs. No existing
run was cancelled or restarted. After the next completed run, inspect the last
printed test and stack trace before changing concurrency or increasing timeouts.
Keep WSL checks sequential and focused under cgroup memory/CPU/time limits; do not
run the entire suite locally merely to reproduce a CI hang.

## Root cause obtained from the bounded run

Run38572877606 failed at the10-minute deadline and exposed a thread trace in
`test_recording_session_delivery.py:receive`, reached from Starlette's response
disconnect listener during the slow-transfer cancellation test. The synthetic
receiver returned an empty completed HTTP request on every call, with no await.
A consumer repeatedly awaiting it could monopolize the event loop, preventing
the session-revocation guard and asyncio timeout from running.

The test now creates a receiver per request: it returns the request body once,
then waits cancellably while the simulated client remains connected. A regression
test verifies no repeated body and cancellation cleanup.39 focused delivery/range
tests passed under192MiB/no-swap limits (113.5MiB peak). No production delivery or
camera code changed for this correction. CI confirmation remains required.

Also override the repository's `addopts=-q` in the CI command: otherwise `-v`
only cancels quiet mode and prints module progress rather than individual tests.
The command is now `pytest -o addopts='' -v -o faulthandler_timeout=60`.
