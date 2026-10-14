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
