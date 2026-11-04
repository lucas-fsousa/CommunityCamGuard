# Recurring unavailable camera: missing startup owner — 2026-11-04

## Evidence and prior acceptance gap

The new report is NOT another observed encoder failure. Before any restart, the
ordinary API reported camera3 native/active73, `runtime_missing`, unavailable and
not recording. The app had started at2026-11-04T10:32:18.543160925Z; capability
snapshot92 still contained valid identity evidence. No clean restart checkpoint
was pending and native-events was empty because this process never launched the
source. Both legacy cameras remained online/recording.

The earlier encoder SIGTERM acceptance tested recovery inside a live app, not
container death or restart without clean shutdown. Treating that result as closure
of the broader recurring-offline problem was insufficient. This report requires
no new Frida/session capture from the user; the missing piece is server lifecycle.

## Change

The exclusive private database owner lock now records a bounded Linux PID1
lifetime (boot ID, PID-namespace inode, process start tick). A later owner must
first acquire the same OS lock, then prove it is a DIFFERENT namespace-init
lifetime before reconstructing abandoned native intent. A still-running owner,
same process, absent/invalid old receipt or unsupported host layout cannot grant
that permission. Camera model/firmware/enrollment review is still mandatory;
legacy cameras are never implicitly migrated and capacity stays one.

This uses the kernel's namespace-init retirement rule, not an assumption that a
changed PID means arbitrary host orphans exited. See the Linux man-pages project,
[pid_namespaces(7), namespace init](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html).
The default image runs the app as Linux PID1. Non-PID1 host / alternate --init
layouts retain clean-checkpoint restart only; arbitrary host orphan reclamation
is not implemented by this change.

Cold Docker startup also owns one bounded readiness waiter, separate from the
capture worker: camera/network warmup no longer expires source preparation after
30seconds. Intent stays transition/unavailable until real shared H264 fragments
are ready, then publication releases the existing gates. One account/decoder,
no fallback/duplicate capture, no thread per retry. Shutdown cancels and joins
the publisher before releasing the service owner. Authentication/evidence and
unconfirmed cleanup still do not become grants.

An older image has no namespace receipt, so upgrading that existing stranded
state needs one trusted operator bootstrap. Afterward ordinary restart/crash
recovery uses the persisted receipt, not a manual installer or emulator.

## Checks

84 focused tests passed under192MiB/no swap/50% CPU (141.6MiB peak), Mypy382/Ruff
passed. Coverage includes live-lock exclusion, malformed/missing/same-process
receipts, unsupported hosts, review denial, one-use generation checks, ordinary
ASGI startup WITHOUT a clean checkpoint, delayed media readiness and publisher
shutdown without late route publication. Fixtures are synthetic, no hardware
actions. Camera-backed abrupt-container acceptance is required below; the old
encoder-only test is not substituted for it.
