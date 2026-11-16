# Native lease retirement overrun — 2026-11-16

## Reported incident

Garage and yard were `native/blocked`, unavailable and not recording, while the
test camera remained native/active. Container was running, OOM=false, restarts=0.
Logs showed local consumer `TimeoutError` followed by source `P2PProbeError`.
Retirement diagnostics had no completed lease/channel outcome, a stopped consumer
and no decoder cleanup failure. This is not a browser-cache/player-only failure.

`LeasedReadSession._await_retirement` cancelled the exact lease, waited five
seconds, then raised while its sole account reader still owned cleanup. The
exception permanently blocked `SourceRegistry` and the source policy; the late
outcome was no longer collected by that source. Even a later clean release could
not reach `RecoveringSource`'s normal retry path. Raising a local deadline is not
evidence that the account transport is unsafe forever, nor evidence it is clean.

## Correction

Keep the normal five-second camera-local grace. If the exact cancelled/claimed
lease still has no result, request retirement through its account inbox rather
than abandoning the result. Only an existing cancelled, claimed, unfinished
ticket in this generation can request that transition. No stale ticket, new
source, browser, authentication call or additional worker can do so.

The sole reader sees that request and uses its existing `MediaAccountRetirement`
path: motor cleanup if applicable, media teardown, broker release and physical
socket close, then terminal lease outcomes. The caller waits a further bounded
ten seconds for that actual outcome, not an optimistic success flag. A normal
clean/physically retired return allows the existing recovery wrapper to retry
with its usual backoff and fresh evidence. This may briefly interrupt siblings
when camera-local teardown is stuck; it must not start another account alongside
the old one. Normal fast per-camera retirement still preserves siblings.

Missing receipts, failed decoder/socket cleanup or an unresponsive owner still
fail closed after the bounded escalation window. No registry reservation is
deleted manually, no checkpoint is forged and no recording files are touched.
Timeout-trigger diagnostics now also log the already-sanitized decoder category
and exact-lease detach flag. The initial decoder stall remains distinct from the
bug that prevented recovering from it.

## Checks

- Late retirement beyond the former five-second deadline is collected, not lost.
- Only a cancelled, claimed exact-generation ticket can escalate.
- Synthetic account worker publishes no terminal receipt before socket close;
  all existing channel handles are retired once before caller results appear.
- Integrated lease + recovery wrapper + source registry recreates its source
  after a simulated decoder timeout and late owner close, without a container or
  registry reset. The former implementation fails this scenario at its deadline.
- A genuinely unresponsive owner still cannot grant cleanup or reuse its slot.
- 86focused lease/worker/PTZ/account/recovery/runtime/legacy-fixture checks passed
  under160MiB/no-swap/25%CPU (75MiB measured peak). Ruff and app-wide mypy passed.

No sound, light, movement, camera reboot or real fault injection was performed.
Deployment and real-stream observation are recorded separately after completion.
