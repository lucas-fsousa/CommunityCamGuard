# Owned-ingress retirement coordinator — 2026-10-23

Status: the **retirement half** of source handoff is implemented and loopback-
tested. It is not invoked by startup or an API. Explicit native target preparation/
activation now follows through [driver offers and readiness](native-source-activation.md).
First migration from the legacy shared hub remains open. No production source or
policy changed.

`media/owned_retirement.py` takes a trusted `OwnedRun`: the exact bound worker and
its caller-owned completion Future. The run thread must span worker lifetime, as
required by its Linux parent-death guard. The coordinator creates no thread or
factory. It rejects missing/nonmatching bindings, shared legacy ingress, stale
revisions, pre-existing consumer holds and overlapping retirement operations.

For one configured, active owned source, retirement proceeds in this order:

1. Commit the target `owned_rtsp`/`native` **transition intent** with revision CAS.
2. Hold browser admission and await registered-operation drain; then suspend and
   retire the recording child through its existing gate.
3. Withdraw the runtime binding, signal the worker and await its completion
   (at most6s). Require its owned child handle to be gone after wait/reap.
4. Discard the retired binding and verify the transition revision still matches.
5. Return `RetiredIngress`, retaining both consumer holds and transition intent.

This result reports local retirement, not a driver capability grant, an externally
supplied activation credential or proof of firmware TEARDOWN. No replacement is
constructed by retirement itself, no policy is marked active and no consumers resume. New-source
preparation must check the actual driver's device support, reserve its resource
domain, install the matching hub/native route and establish cleanup ownership
before committing activation and releasing consumers. Native audio/recording and
endurance acceptance remain separate.

The coordinator now retains at most32 issued receipts. Its internal `claim`
consumes the exact object once after checking current policy and confirmed holds;
a reconstructed report is not a preparation permit. The native activation adapter
uses that claim, not a client-provided success flag or stream deletion.

## Failure behaviour

Any failure after transition commit latches the coordinator and retains the
worker/Future references. A matching policy is marked blocked; persistence failure
or a newer revision does not erase the local latch or overwrite that revision.
Acquired admission holds remain. No receipt, replacement, automatic fallback,
factory replay or optimistic recovery is provided. A failed browser drain can
leave the old recording/source running; that is not success and must not authorize
replacement. Interruptions retain failure state and are re-raised.

Preflight failures do not mutate policy or signal a process. Budget validation is
also preflight. No direct-camera legacy source can obtain this worker receipt:
deleting its name from the old shared go2rtc registry is still insufficient. The
first shared-ingress migration and runtime reconciliation exclusion need a separate
controlled procedure, not a global restart hidden inside this coordinator.

## Evidence

Thirty-five focused retirement/gate/binding cases passed under192MiB/no-swap/
50%-CPU limits, peak125.1MiB. A real pinned go2rtc worker connected to one metadata-
only loopback camera. The coordinator reaped it and observed connection closure
before a replacement metadata connection; peak simultaneous upstream connections
stayed one. No audio/video frames or physical camera traffic occurred.

Failure cases cover drain timeout, recorder cleanup, Future wait, unreaped child,
binding discard and failure to persist a blocked phase. They return no receipt and
reject another retirement through the latched instance. Independent-camera policy
and admission are preserved. Fake process handles never send an OS signal.

The expanded58-case retirement/native/recording/persistence run passed at150.7MiB
with zero swap. Native direct/alias sockets drain their readers without stopping
the shared source, then resume against that same owner. At commit `fe077a1`,
[CI](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40043825688)
passed4949 Python tests (one skipped), type/lint and all frontend contracts;
[Image](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40043825648)
passed the amd64 build and restricted worker smoke. Neither workflow deploys or
establishes camera/native-playback acceptance.
