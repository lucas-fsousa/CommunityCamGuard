# Explicit post-retirement native activation — 2026-10-23

Status: driver offers, preparation, readiness, publication and failure cleanup are
implemented and offline/loopback-tested. No source-selection API, startup service,
dashboard switch, production deployment or real-camera activation was added.
The first legacy shared-hub migration remains separate.

## Driver boundary

`CameraDriver.native_media_offer(camera)` defaults to `None`. A driver may return
a `NativeSourceOffer` with the opaque camera ID, resource domain, lazy factory and
a bounded local evidence validator. Core never sees vendor credentials or infers
support from brand, MAC, port, browser input or a generic capability boolean.

Yoosee's offer requires all of:

- Explicit backend rollout of the `native_video` operation for this exact unit.
- A reviewed exact-identity profile containing one read-only `native_video` proof
  with `hevc` in its tested options, including the existing source-digest provenance.
- A current backend identity snapshot matching model/firmware/SDK/hardware.
- The exact registered camera/enrollment association and credential generation.

This reuses the existing trusted profile/rollout/snapshot stores. No production
proof is invented, registered or copied between cameras. A fresh snapshot is only
an identity-freshness requirement, not native-video proof. Enrollment alone never
grants support. Credential rotation invalidates an in-flight offer; a newly captured
offer may still qualify for the same reviewed unit. Reassociation, changed identity,
revoked review and unknown/expired evidence reject it. No network refresh occurs
while querying an offer; its representation excludes factory, domain and validator.

## Ordered activation

1. The owned-retirement coordinator must have issued the exact in-memory receipt.
   `claim` verifies its identity, current transition revision and confirmed browser/
   recording holds. It consumes the receipt once; an equivalent serialized/copy
   object cannot replay it. There are at most32 outstanding receipts.
2. `NativeActivation` submits one owned run to a caller-supplied executor. The
   caller supplies thread/resource quotas; no per-client or default worker is made.
   `SourceRegistry` reserves camera and driver domain before the lazy factory.
3. Readiness borrows the shared output, checks baseline H264 MP4 initialization
   and a completed fragment, then closes that temporary reader. No second camera
   connection, decoder or encoder is created for this check. The readiness deadline
   is finite (default5s, maximum30s); no hidden retry authenticates a new source.
4. Under the prepared-run lock, revalidate evidence, CAS the policy active, invoke
   the trusted server's route/config publication callback, recheck evidence/run
   liveness, then release browser admission. The server must publish the same source
   registry into its native delivery path; there is no implicit process-global one.

Source submission and Future tracking are atomic with cleanup. Closing before
submission prevents a late job; closing during submission waits until the handle
is tracked. Callback registration occurs outside the state lock because an already
finished Future may invoke synchronously.

## Runtime and failure rules

SourceRegistry uses an opaque internal owner identity for readiness and stop. A
failed admission cannot borrow or terminate another camera/generation's source,
even if it has a ready MP4 ring. The existing unqualified browser subscription
still reads the one published shared source.

Evidence is checked at construction/publication and at most once/second on the
source-run thread, not once per frame. The periodic callback performs local reads
only. Evidence connections close explicitly, without waiting for Python GC.
Unknown evidence cancels this run; there is no silent recertification or factory
replay. A completion callback serializes with publication, holds browser admission
and blocks the matching durable revision, including natural source termination.
It never reopens a gate after another thread reports failure.

`PreparedNative.close()` blocks admission, signals only its owned run, awaits its
Future and drains registered consumers. Unconfirmed cleanup retains the handle and
registry reservation. Activation errors similarly cancel/await before propagating;
no automatic fallback, consumer resume or optimistic reset is provided.
Confirmed close is serialized/idempotent; repeated cleanup or a delayed completion
callback cannot re-hold a newer generation's viewers. Failed close retains ownership
and may be retried, never marked closed optimistically.

The current Yoosee shared session deliberately yields to foreground control demand.
That can end a native run; this adapter handles completion by blocking, not by
silently reopening/authenticating it. Coordinated resumption after foreground work
must be validated/implemented before replacing the production RTSP experience.

**Recording stays suspended:** this is experimental video delivery. Native audio,
recording timestamps and archival input are not integrated yet. Releasing the old
RTSP recorder against a missing hub source would be incorrect. None of these paths
is enabled in the current deployment, and no physical camera was contacted.

## Evidence and remaining work

54 activation/offer/retirement/registry cases passed at160.5MiB with zero swap under
192MiB/50%-CPU limits. They cover forged/replayed receipts, stale/missing evidence,
readiness/publication failure, unknown cleanup, owner collisions, completion races,
submission/close races, shared readers and delivery through the dashboard socket.
A real pinned RTSP worker is reaped before the synthetic native factory runs; the
fixture camera sees only one old metadata connection. New native output in these
tests is synthetic structural MP4, **not decodable camera footage or physical
playback acceptance**. Another102 routing/source/lifecycle regressions passed at
149.7MiB, and27 evidence-store regressions passed at126.2MiB, all with zero swap.

Still required: deployment orchestration/reconciliation exclusion, controlled first
legacy migration, actual reviewed native-video records for the tested enrollment,
real native browser/endurance acceptance, audio/recording integration and ARM64
acceptance of this new lifecycle. No new HTTP route permits uploading proof or
passing a source factory.

At commit `4830015`, both
[CI](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40046885943)
and [Image](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40046886022)
passed. The code's amd64 image builds and its restricted worker smoke passes;
this is not an ARM64 or physical native-camera acceptance claim.
