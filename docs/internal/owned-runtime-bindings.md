# Owned RTSP runtime bindings — 2026-10-23

Status: runtime consumers integrated and offline/loopback-tested. No default
binding registry, source scheduler, camera migration or production deployment.

## Contract

`media/owned_bindings.py` binds a public camera ID and durable active `owned_rtsp`
revision to an already-ready `OwnedRtspIngress`. Capacity defaults to one, accepts
at most32, and includes withdrawn/unreaped entries. Attach requires a configured
camera, active owned intent, a ready child and an empty reservation. It cannot
launch a worker, authenticate a camera, grant driver capabilities, or prove that
old legacy ingress has been retired.

Each `BoundIngress` is a distinct runtime generation. The renderer and new reader
admission require matching durable revision and a still-visible worker URL/PID.
`withdraw` immediately hides the binding but does not stop the process or release
capacity. `discard` requires the owned child handle to be gone after reap; merely
signaling stop or hiding the URL is insufficient. Unknown cleanup remains reserved.
Old viewer leases retain their binding identity and cannot follow a later binding
under the same persisted revision. These ephemeral bindings are never serialized
into SQLite; a fresh server without them fails closed for persisted owned intent.

The worker exposes a random generation-specific path, preventing an old hub retry
from joining a new worker if the OS recycles its port. Neither this path nor a
binding is a substitute for authentication: the RTSP boundary remains loopback.

## Integrated consumers

- `Go2rtc(owned_ingress=...)` writes the bound local URL as the base stream.
  Existing HD/web variants derive from that base and never from the camera URL.
  No matching binding means omission, never direct-camera fallback. Policy's read/
  apply transaction and the binding snapshot lock span the actual config write;
  withdrawal cannot return while an earlier snapshot is still being written.
- `Recorder(owned_ingress=...)` permits a new child only with active legacy intent
  or a matching ready owned binding, while retaining storage pause and per-camera
  holds. Its input remains the hub's base restream, with existing full-quality
  video-copy/UTC recording behaviour. The binding admission lock spans child
  creation/tracking, so withdrawal waits for an already-admitted birth. No second
  direct camera connection is added.
- Primary and temporary viewers use the media engine's explicitly supplied
  binding registry. Their leases include the revision and binding identity;
  withdrawal/unready state invalidates the existing session guard. Temporary
  clients still negotiate only the restricted MSE protocol.
- Recovery can use a matching owned hub binding. A missing/withdrawn binding or
  transition/blocked intent rejects recovery instead of reaching the old hub.

## Handoff still required

Supplying the optional runtime registry is not an activation procedure. Startup
does not create it. The [retirement coordinator](owned-ingress-retirement.md) now
composes consumer holds and physical owned-worker retirement. The remaining target
activation must own per-camera/driver admission,
use the [viewer gate](viewer-session-drain.md) and recording holds to gate/drain
consumers, physically retire the previous upstream, prepare the
new worker, commit owned selection, attach the ready worker, apply the matching
hub config and only then release consumers. Old shared go2rtc ingress needs a
controlled first migration; an API DELETE still does not certify physical close.

These per-operation barriers and polling guards are not a complete atomic drain.
A config completed before withdrawal can retain the old local URL until reapplied; generation
isolation makes that stale rather than an invitation to restore the camera URL.
A pre-admitted browser may race with a transition, and an independently negotiated
WebRTC peer may survive signaling closure. The coordinator must await actual
retirement; there is no automatic owned/native/legacy fallback or retry here.
Audio, native recording and hardware/browser endurance acceptance remain separate.

## Evidence

Sixty-two binding/browser/persistence cases passed under192MiB/no-swap/50%-CPU limits,
peak160.2MiB. Concurrent tests hold a file write or recorder birth and verify
withdrawal cannot finish before the earlier operation completes. The real pinned
go2rtc fixture attached a ready worker, rendered its
generation URL, and served two metadata consumers over one simulated-camera TCP
connection. Withdrawal retained ownership until stop/wait/reap, then config omitted
the source. It emitted no audio/video frames and contacted no physical camera.
Other cases cover capacity/duplicates, policy revision changes, missing runtime
state after restart, unready/removed workers, recording admission and both browser
roles closing on withdrawal without changing durable policy. All recorder
processes in these integration cases were mocked, not real FFmpeg encoders.
