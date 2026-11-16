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

## Deployment and observed result

Deployed `c3b93fa`, local image `6cb7c7870e08`, after CI43797467440 and
Image43797467456 succeeded. Only the application container was replaced; no
camera reboot/reset, database unblock or recording deletion. The server-side
native talk diagnostic flag is now false.

All sources returned native/active and available: test camera revision357,
garage86, yard84. A simultaneous120-second HD WebSocket observation of garage
and yard received123/243binary messages and70843830/75209773bytes respectively;
maximum gaps1.254/0.817seconds, no disconnect/errors. Every15-second status
sample showed all three online/recording, with no revision change or availability
violation. This checks delivery and recording activity, not physical image quality
or a claim that future network/decoder stalls are impossible.

Read-only current/previous-hour archive checks confirmed actual file growth:
garage9699328 ->18874368bytes, yard7602176 ->14942208bytes; both latest file
modification ages remained under two seconds at the final check. No footage was
read in full, transcoded, moved or deleted.

An additional bounded before/after synthetic check loaded the previous committed
lease implementation: it raised `P2PProbeError` at simulated6seconds and left
the lease undetached. The corrected implementation collected the delayed clean
outcome at8seconds after requesting owner retirement, without camera I/O. Measured
peak70.2MiB under128MiB/no-swap/25%CPU. Actual physical account-close ordering is
covered independently by the account-worker regression, not faked by this timer
comparison. No real failure was injected into the live cameras for acceptance.
