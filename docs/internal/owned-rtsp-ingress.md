# Owned RTSP ingress boundary — 2026-10-23

Status: implemented and verified with real go2rtc1.9.14 and loopback RTSP fixtures.
This component is not started by application startup or an API. Production camera
sources, containers and recordings were not migrated or interrupted.

## Boundary selected

`media/rtsp_ingress.py::OwnedRtspIngress` runs one RTSP-only go2rtc child for a
public camera ID. It exposes a loopback restream to the existing media hub, which
can continue owning browser transcodes and other consumers. Stopping/reaping this
dedicated child provides a socket-lifetime boundary for that ingress without
restarting the shared hub or relying on DELETE from its stream map.

The worker's fixed configuration enables only the `rtsp` module. The pinned
[go2rtc entry point](https://github.com/AlexxIT/go2rtc/blob/b5948cfb25404cc5cb37b166ecaa2dca20b11d4b/main.go)
supports that allowlist and always initializes its stream registry. No API,
WebRTC, exec, FFmpeg, preload or publish operation is enabled. There is one static
RTSP/RTSPS source and one loopback listener; camera backchannel negotiation is
disabled. The worker forwards media without starting an encoder.

The source is lazy: listener readiness does not connect to a camera. Consumers
share the worker's existing producer. Construction itself performs no source I/O.
Each worker generation now exposes a distinct random128-bit stream path, not a
fixed `/ingress` alias. If the OS later recycles its ephemeral port, an old hub
retry cannot accidentally attach to the replacement generation's stream. This
is generation isolation, not RTSP authentication; the boundary remains loopback.
The coordinator must own camera admission before calling `run`; this component
alone does not prevent a second object or the old shared hub from opening another
ingress. It is not a `ManagedSource` MSE adapter and must not be inserted directly
into the current native-only delivery registry.

## Lifecycle and configuration

- `run(cancelled=...)` executes on a caller-owned thread; no service thread or
  retry worker is created. The object is single-use even after failure.
- Readiness has a5s deadline. `owned_sockets.py` matches the child's socket inodes
  against bounded procfs tables, then requires exactly one IPv4 loopback TCP
  listener. It does not guess a free port or probe somebody else's listener.
  The child must have execed the expected direct ELF binary; shell wrappers and
  the intermediate interpreter cannot supply a false readiness receipt.
- `stop()` signals cancellation only. `run()` normal completion is the receipt:
  TERM/wait2s, then KILL/wait2s if needed. Failed retirement retains the child
  handle, withdraws the restream URL and raises `SourceCleanupUnconfirmed`.
- The JSON config is an owner-only0600 Linux memfd, sealed against writes,
  truncation and extension. It is inherited only by the child. Source credentials
  never enter argv, logs or an on-disk config; the child receives a small clean
  environment rather than the application's keys/tokens.
- The process has at most128 descriptors and no core dumps. `GOMAXPROCS=1` and a
  64MiB Go memory target reduce overhead; **GOMEMLIMIT is not a hard RSS ceiling**.
  The caller/deployment still supplies cgroup memory/CPU limits.

`exec_guard.py` is a short-lived interpreter that arms Linux parent death and then
execs go2rtc under the same PID. It does not leave an extra Python daemon running
and does not run Python `preexec_fn` inside the multithreaded app. Parent identity
is checked around guard setup. Privileged/capability-bearing executables are
rejected because those exec transitions can clear parent-death protection.
Linux sends this signal when the creating thread exits; the caller's thread must
therefore span the child's lifetime. See the primary
[PR_SET_PDEATHSIG reference](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html).

A reaped RTSP-only process confirms its local TCP/UDP sockets are closed. This
does not prove a camera acknowledged RTSP TEARDOWN, stopped transmitting old UDP
packets, or accepted native/P2P replacement. No firmware-side acceptance is claimed.
The tested platform is Linux/amd64; ARM64/Termux validation of this new boundary
is pending and does not invalidate the earlier experimental installation guide.

## Evidence and CI

The local executable is the official1.9.14 release, revision`b5948cf`, SHA256
`32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6`.
CI downloads the same pinned amd64 fixture and verifies its digest before running
tests; these tests do not silently skip on the CI runner.

Real-process tests confirm:

1. The owned listener is distinct from another process's loopback listener; source
   credentials are absent from argv and a sentinel app token is absent from env.
2. Two RTSP metadata clients share exactly one simulated camera TCP connection.
   There are no descendant processes. Reaping the worker closes that connection
   before a replacement connects; an independent listener remains usable.
3. Force-killing an isolated owner kills go2rtc via parent death. A disposable
   subreaper harness verifies SIGKILL and reaps the orphan, without changing the
   server or pytest process's global child-reaping behaviour.
4. Cancellation errors and uncertain waits cannot silently lose an owned child;
   unexpected public/extra listeners or oversized procfs inspection fail closed.
5. Replaying an old generation's path against a replacement listener is rejected
   without opening its upstream. The correct new path connects normally. This
   simulates address reuse without reserving/probing a previously owned port.

Generation-isolation follow-up:28 worker/socket cases passed under192MiB/50%CPU,
peak99.1MiB with zero swap; all upstreams were metadata-only loopback fixtures.

The fixture emits no audio or video frames and uses only127.0.0.1. The expanded
ownership/gate test run peaked at105.7MiB with zero swap, under a192MiB cgroup and
50% CPU quota. This does not certify camera performance or continuous playback.

## Remaining integration

The old shared go2rtc process still pulls production camera URLs directly. This
new receipt applies only to an ingress started inside this boundary. A
[durable intent guard](durable-source-policy.md) now protects config regeneration
and new recording admission. [Runtime bindings](owned-runtime-bindings.md) now
connect ready workers to hub/recording/browser consumers. Before live selection/
fallback, finish atomic admission/drain and migrate the old ingress under a
controlled procedure with verified retirement.
Recorder suspension is already available through `Recorder.ingress`; native MSE
delivery is separately implemented. Existing hub config/address recovery must use
the selected owned loopback source rather than restore the camera URL. Native
audio/recording integration and real-camera acceptance also remain pending.

## Artifact packaging

`build_tools/install_go2rtc.py` is now shared by Docker builds and CI. It selects
the official pinned1.9.14 amd64/arm64 artifact, streams it within a32MiB byte budget,
checks SHA256 and publishes a verified executable atomically. Different existing
files and output symlinks are not overwritten; normal failures clean temporary
files. A matching executable is reused without downloading it again.

The ARM64 artifact is `go2rtc_linux_arm64`, official SHA256
`359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50`.
Both digests are from the [pinned upstream release](https://github.com/AlexxIT/go2rtc/releases/tag/v1.9.14).
The Dockerfile uses `TARGETARCH` and supplies `/app/bin/go2rtc`; a non-BuildKit
build can use the build container's detected architecture. No Go toolchain/native
compilation or persistent supervisor package is added.

The path-filtered Image workflow builds the amd64 app image on GitHub and checks
imports plus the owned worker lifecycle with network disabled, all capabilities
dropped, a read-only filesystem and256MiB/0.5CPU limits. This is an image smoke
test, not streaming/hardware acceptance; ARM64 image/worker behaviour remains open.
No production image is rebuilt/restarted by that check. The current image running
on the server does not gain this binary until an explicit rebuild.

Packaging acceptance at commit `7a67f11`: both
[CI](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40010187514)
and [Image](https://github.com/lucas-fsousa/CommunityCamGuard/actions/runs/40010187533)
passed. CI reported4863 Python tests passed and one skipped, followed by all Node
contracts. Image built the real amd64 app container and completed the restricted
import/listener/stop/reap smoke above. This verifies packaging and that local
lifecycle, not live camera playback, ARM64 execution or production migration.
