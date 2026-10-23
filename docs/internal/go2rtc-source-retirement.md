# go2rtc source retirement audit — 2026-10-22

Status: production RTSP remains unchanged. Native admission is implemented
internally, but **source switching is not safe to activate yet**.

## Evidence

Read-only Docker image metadata identified the running go2rtc image as 1.9.14,
revision `b5948cfb25404cc5cb37b166ecaa2dca20b11d4b` (configured tag `latest`).
No API mutation, process restart, or camera test was performed for this audit.

The pinned upstream implementation matters:

- [Streams API](https://github.com/AlexxIT/go2rtc/blob/b5948cfb25404cc5cb37b166ecaa2dca20b11d4b/internal/streams/api.go):
  DELETE removes the map entry and patches configuration; it does not explicitly
  stop that stream's producers. PUT/PATCH are not retirement acknowledgements.
- [Stream lifetime](https://github.com/AlexxIT/go2rtc/blob/b5948cfb25404cc5cb37b166ecaa2dca20b11d4b/internal/streams/stream.go):
  consumer removal invokes producer cleanup, subject to pending work and remaining
  sender references. Absence from the streams map is not evidence of socket closure.

Consequently, deleting a stream and immediately opening native ingress could leave
the old camera connection active. A global restart is not an acceptable per-camera
handoff: it affects unrelated recordings/viewers and is not a scoped close receipt.

## Local integration constraints

`media/go2rtc.py::build_config` still derives base RTSP sources from the registry.
HD/web encoders consume those restreams. `release_live_producers` only handles
legacy quality preloads, not every base-stream consumer or recorder. Config
regeneration would also restore RTSP unless source selection becomes durable.
The native `SourceRegistry` does not currently own these production producers.

Before enabling native selection/fallback:

1. Gate new viewers, recorders and reconnects for the selected camera only.
2. Retire every existing consumer and establish a verifiable per-camera producer
   close boundary; do not treat a missing API entry or zero counters as proof.
3. Persist source selection so config regeneration/address recovery cannot reopen
   the retired RTSP ingress concurrently.
4. Admit native ingress only after closure; use the same barrier before fallback.
5. Test delayed/failed retirement and concurrent recorder/viewer reconnection
   offline before any explicitly authorized camera-3 deployment.

The [owned RTSP ingress boundary](owned-rtsp-ingress.md) is now implemented and
verified with real go2rtc1.9.14 against loopback fixtures. RTSP-only workers can
provide a per-camera process/socket retirement receipt. Production still uses the
old shared direct-camera sources, so controlled migration, durable selection and
consumer gating remain necessary. No DELETE/restart workaround was introduced.

[Durable source intent](durable-source-policy.md) now prevents config regeneration
and fresh recorder instances from restoring a selected-away direct camera URL.
The live coordinator, runtime bindings and old-consumer migration remain pending;
persisting a choice alone never certifies physical retirement.

The [recording-consumer gate](recording-ingress-retirement.md) now implements
per-camera admission suspension and confirmed recording-child cleanup. Resync and
storage recovery cannot release that hold. This is part of item1 above; it does
not certify closure of the upstream go2rtc producer or complete the other items.

## Independent session cleanup fix

`session_channels.run_guarded` already cancels media/intercom work on revoked or
expired authorization. Its WebSocket close now has a one-second timeout, so a
cooperative ASGI close stalled on transport cannot retain the guard indefinitely.
Tests cover blocked/failed close and verify task cleanup. This bounds the close
await, not arbitrary cancellation-resistant application code or physical camera
teardown; it is not a substitute for the ingress retirement barrier above.

2026-10-23 follow-up: the same bounded helper is used by the primary media proxy,
temporary-access MSE bridge and intercom finalizers. Bounding only the guard's
close was insufficient: a cancelled operation could itself wait forever in its
own final close while the guard awaited task cleanup. Regression tests cover this
second close and preserve parent cancellation without leaking child tasks.

Managed-process follow-up: `Go2rtc.stop()` now waits after escalating to `kill`.
It retains the process handle and propagates a failed termination wait, preventing
that managed restart from immediately spawning a replacement. This only confirms
the owned parent was reaped. It does not certify child FFmpeg retirement, external
container closure, or per-camera retirement; the handoff blocker above remains.
