# Native listen-in and archive — camera3 delivery trial

## Scope

This extends the [real native dashboard trial](native-dashboard-live-trial.md),
not a second camera capture or a production capability rollout. The operator-only
`scripts.native_dashboard_trial --native-av` uses the same encrypted enrollment,
retirement proof, existing authenticated dashboard socket and rollback procedure.
No speaker, microphone transmission, light, siren or device setting is invoked.

Only the sampled camera3 format is admitted: native HEVC1080p plus codec4/option2
AAC-LC ADTS,16kHz, mono,16bit,1024 samples. Header and each ADTS frame must agree.
Other models/formats need driver evidence; sharing a brand is not sufficient.
Ordinary video-only source construction and production startup are unchanged.

## One source, one process

The account/socket reader hands off bounded video and audio references; it never
runs FFmpeg or writes recording files. Audio has its own128-frame/256KiB ceiling;
overflow fails the trial rather than silently dropping audio and hiding a gap.
One consumer owns one FFmpeg. A local MPEG-TS wrapper carries both tracks and
their microsecond timestamps on one pipe, avoiding a two-input-pipe startup deadlock.
Native PTS are rebased at the configured video IDR, not interpreted as camera UTC.
Older audio is discarded before that IDR. Audio batches advance by1024/16000s.

That one process produces:

- H264 Baseline plus copied AAC, fragmented MP4 shared by dashboard viewers.
- Original HEVC plus copied AAC in60s archive segments, with UTC wall-clock paths
  matching the existing recording tree. No parallel camera3 RTSP recorder runs.

The browser init parser accepts exactly one H264 and optionally one AAC-LC track.
AAC is read through the ES/DecoderConfig/AudioSpecificConfig descriptor hierarchy;
missing configuration, non-LC or duplicate tracks are rejected. Clients must
explicitly advertise AAC support. `delay_moov` is necessary for copied ADTS: writing
the init before the bitstream filter sees its first frame omitted AudioSpecificConfig.
Video-only muxer timing/flags retain the previous tested behavior.

The initial trial rejected decoder epoch replacement instead of risking same-second
filename overwrite. Decoder-epoch naming is now isolated as described below; full
account/source recovery and control coexistence remain required before continuous
deployment.

## Evidence and limits

Passive samples E/F captured128 real AAC frames. FFprobe identified AAC-LC16kHz
mono. Native video/audio raw timestamps share a microsecond clock; sampleF first
audio offset was140ms, with video/audio spans8.397s/8.288s. These are not UTC dates.

File-only integration with real private HEVC/AAC samples produced five browser
fragments with MIME `avc1.42C028,mp4a.40.2` and an original HEVC/AAC archive. Peak
RSS119.3MiB, hard320MiB cgroup, no swap. Samples are ignored/private, not public
fixtures. Synthetic input durations differ; this test is codec/mux acceptance,
not proof of live synchronization or physical audio quality.

Live trialG reached authenticated dashboard A/V readiness and a real socket
received an init plus one fragment, but the source ended early with cleanup
unconfirmed. It is **not** a successful sustained/recording acceptance. Original
containers/config/directory ownership were restored. Investigation continues with
safe failure-site diagnostics (no exception messages, media or credentials).

Live trialH subsequently completed90s, nine10s samples with native running and
the other two recorders active at every sample. FDs40–70; peak281.2MiB within
768MiB hard limit, zero swap; CPU55.156s over1m49.701s including migration/rollback.
The existing authenticated socket negotiated both codecs and returned an init
plus four consecutive A/V fragments. That capture decoded both tracks successfully.
Browser logs recorded initial waiting/playing, without a later camera3 stall event
during this short interval. This is telemetry, not a new human listening acceptance.

The same FFmpeg wrote two real camera3 archive segments: HEVC1920x1080 + AAC16kHz
mono, video durations63.298667s/27.841s, audio63.223s/27.843s. First-segment audio
started156ms after video; second-segment tracks both started at zero. The first
segment fully decoded without errors (56.7MiB decoder peak). Segment boundaries
follow source keyframes, so60s is a target, not an exact duration. UTC filenames
and folders matched the existing indexer format. Original deployment, configuration
and directory ownership were restored; the primary camera enrollment/policy was
not replaced. Private footage remains local/ignored.

This establishes the short real A/V/archive delivery, not long-term availability:
trialG's intermittent early exit remains unresolved, and controls currently yield
the shared owner rather than safely resuming it. Those are the next delivery tasks.
Failure diagnostics now retain the worker's fixed reason and independent broker
release/TCP CLOSE/socket-close facts even when cleanup raises. Unknown results
remain unknown; this does not relax the cleanup barrier or trigger another login.
Archive shutdown now also allows a bounded1s EOF flush while draining discarded
live output, then retains the existing terminate/kill fallback. Archive consumer
join is bounded at3s; ordinary video-only shutdown retains its2s join.
File-only incremental replay verified that finalization path with15s of real
archived input: eighteen live fragments before EOF, confirmed child exit and
HEVC/AAC archive durations15.062667s/14.848s. Peak147.1MiB with256MiB hard cap
and zero swap. The earlier400ms allowance lost the pending tail under the50%
CPU replay quota; the1s allowance passed. This is a bounded grace, not permission
to replace a child whose termination remains unconfirmed.

After rollback, the normal production recording index listed both native segments
as `Câmera 3`, with UTC `started_at` values. Authenticated original-file requests
returned HTTP206 for `Range: bytes=0-63`, with the expected MP4 header. That verifies
archive discovery and serving, not a new claim about browser transcoding latency.

## Read-only control coexistence

Native sources now publish a driver-private run-lifetime reference to their
already-owned read session. Existing semantic GETs for speaker volume, protection
state and protection schedule can submit `ProWritable.volume` or
`ProWritable.guardParm` to that account's bounded mailbox and validate the correlated
reply without authenticating again. The explicit state-read scope adds only these
two B7 roots; identity-only workers retain their old scope. No volume write, arbitrary
model path, speaker playback or public raw-command API was added.

An active native owner with stale credentials, no readiness, a timeout or invalid
reply fails the operation: it never falls back to a second login. Driver capability
catalogue checks still run first, including expiry and exact-device proof. Broker
authentication is reported honestly; a direct camera handshake is not fabricated.

TrialI exposed another real interaction: the catalogue's automatic capability
refresh used a blocking account reservation and displaced native media when a
snapshot expired. Background refresh now reserves nonblocking **before** snapshot
invalidation/network I/O. It defers when another owner is active; existing expiry
and retry-backoff rules remain. This does not make stale evidence a capability grant.
Refreshing all capabilities through the shared session is still pending.

After ordinary read-only capability collection while native media was idle,
trialJ ran60s of native A/V/archive. Three authenticated existing dashboard GETs
returned verified volume50% in187/65/83ms. The same native source revision2 remained
running after each query; all six10s samples retained the two other recorders.
The source completed the trial and rollback cleanly; peak236.7MiB, CPU19.433s over
76.962s including migration/rollback, with the768MiB/zero-swap bound. No speaker,
light, movement, reboot or camera setting was changed. This proves volume-read
coexistence only, not all controls/PTZ or multiple simultaneous native cameras.

TrialK subsequently queried all three semantic controls through the existing
authenticated API while native A/V/archive remained active. Replies were verified,
authenticated and application-confirmed in108/84/89ms; every follow-up manifest
still reported the same native source at revision2. Guard state/schedule were
decoded with the existing typed parsers, not guessed from another field. No state
or schedule was changed. All six10s samples retained both other recorders; the60s
trial restored the deployment cleanly, with238.2MiB peak and19.209CPU seconds over
81.772s including migration/rollback.

The semantic controls adapter now rejects unintegrated writes and
resource-option requests for that active native camera instead of displacing its
owner to authenticate separately. Ordinary RTSP/idle operation retains existing
behavior and the hardware capability catalogue is unchanged. Shared write/B9/PTZ
families, shared capability refresh and recovery still need implementation; this
is not a claim that the complete control panel can run alongside native video yet.

### Lamp-state query on the same account

The remaining existing readable control, white-light state, now uses a dedicated
bounded type12 B9 job on that same sole broker reader. It does **not** infer lamp
state from another B7 property and cannot build type11 writes. Request and BA
receipt sequences come from the account-wide namespace; the job does not close
that namespace. Peer/session/generation/access/device/request-ID/type correlation
and the existing typed binary-state parser all apply. A positive transport ACK
alone never completes the operation.

The driver handoff has one slot, an absolute2s deadline and a2.5s API wait. Timeout
and cancellation never trigger a write, retry, login or camera STOP. Output uses
the existing account budget without borrowing media/PTZ cleanup reserves. Message
ACK ownership is explicit: the typed job's response is not acknowledged again by
the generic dispatcher path. The synthetic encrypted worker test combines media
OPEN/CLOSE, B7, lamp B9 and heartbeat with six distinct allocated sequences and
exactly one lamp message ACK and one BA receipt.

TrialM automatically ran the existing authenticated four-control API probe at
native readiness. Volume/protection/schedule/lamp replies took68/92/90/170ms; all
were verified and application-confirmed, and the same native revision2 was running
after each. The lamp reported OFF; no type11, speaker, siren, movement or configuration
write was sent. All six10s samples retained native running and both other recorders.
The60s trial and rollback completed without cleanup errors, with223.6MiB service
peak and20.287CPU seconds over76.618s including migration/rollback. The source and
probe shared the768MiB hard cap; hub/permission helpers remain separately capped.
TrialL ran the same A/V path but its manual API probe missed the window; it is not
counted as lamp-query acceptance. TrialM's automatic readiness probe avoids that gap.

This completes same-session integration of the four currently exposed semantic
state getters for the test unit, not writes/PTZ, resource catalogues, all capability
refresh reads, multiple native routes or automatic recovery. Ordinary production
was restored and still uses its existing source mode.

## Shared PTZ implementation — 2026-10-26, synthetic acceptance only

The existing dashboard PTZ dispatcher can now submit a bounded gesture to its
camera's active native media owner, without preparing another route or logging in
again. This requires an explicit driver-supplied `PtzGrant` for the exact device,
current enrollment access ID and reviewed directions. A normal media session still
has no motion permission. Neither the production source factory nor the operator
trial supplies this grant yet; no UI capability was widened and no real motion was
sent for this implementation.

The account retains one UDP reader, sequence allocator and output budget while its
cooperative TCP pump delivers media. Queued gestures cannot START before TCP media
readiness. On cancellation or teardown, the existing reserved STOP is drained on
the old broker socket **before** potentially slow TCP/media closure. Failed STOP
delivery blocks replacement; closing the media route cannot make it successful.
An HTTP timeout abandons the ticket to the socket owner, not the motor cleanup.
The one gesture slot becomes reusable only after safe completion. Existing STOP
requests keep their cancellation hook even if the selected profile is unavailable.

An active native owner without a ready grant rejects the gesture; it never falls
back to ONVIF or a second native login. The same guard covers unsupported directions
and missing profiles in the driver. Idle/ordinary RTSP operation retains its
existing behavior.

Encrypted synthetic tests combine all four directions, media pumping, B7 and lamp
B9 reads and heartbeat with distinct account sequences. They cover early STOP,
failed START send, interrupted delivery, missing STOP receipt, failed TCP closure,
handshake readiness, stale credentials and caller timeout. The focused regression
group passed192 tests under a192MiB memory cap, swap disabled and50% CPU quota
(113.9MiB observed peak). This is not physical PTZ/video coexistence acceptance.
No camera connection, container restart, light or sound command was performed.

Next: driver-reviewed admission and a bounded camera3 coexistence trial, then
remaining shared writes and coordinated recovery. Default native activation,
multiple native routes and permanent production rollout remain unchanged/pending.

## Reviewed factory admission — 2026-10-28

`native_media_offer` now translates a separate `native_media_ptz` operation proof
into the exact-device `PtzGrant` used by the shared-session implementation above.
The registered profile and explicit rollout must agree on the exact model/firmware
identity, with a current snapshot. The proof is write-only and its nonempty options
must be a subset of the four reviewed directions. Enrollment, the read-only video
proof and ordinary PTZ support never imply combined-session motion permission.
No such real-camera proof was registered by this implementation; default sources
and the operator trial remain without motion grants.

The offer captures the resolved scope. Removing rollout, changing directions or
credentials, or expiring/invalidation of the snapshot invalidates that offer before
construction. A granted source also keeps the local revalidation callback: an
already constructed session checks it before each new gesture, not merely once at
startup. Only the literal `True` admits; unknown/stale evidence refuses START without
another login or ONVIF fallback. The callback performs local database reads, never
capability refresh/authentication. It is outside the account lifecycle lock.
Revocation does not block cancelling or draining the STOP of an existing gesture.

Tests exercise both the real factory-to-session callback and its admission boundary:
read-only/ambiguous proofs, wrong directions, missing activation, ordinary PTZ
proof, snapshot invalidation, expiry, review withdrawal and credential rotation.
The221-test focused regression group passed under192MiB/swap0/CPU50%, with145.2MiB
peak. Mypy354 files passed under256MiB, observed122.4MiB. Ruff passed. These are
synthetic checks, not physical PTZ/video coexistence acceptance.

The stable server now runs on Termux independently through its dedicated tunnel.
This WSL development change was not deployed there, and no media/camera action or
restart was performed. The next real camera3 trial must explicitly release that
camera's capture on Termux before WSL connects; the earlier workstation-only
shared-hub trial is not a safe phone handover mechanism.

## Decoder-epoch archive recovery — 2026-10-28

The native A/V source now gives every decoder epoch a UUID-derived filename suffix:
`%Y%m%d_%H%M%S_e<32 lowercase hex digits>.mp4`. Existing UTC day/hour folders are
unchanged. Distinct decoder instances, including replacements started in the same
wall-clock second, no longer target the same archive filename. Constant filenames
are rejected before constructing the session. This isolates decoder restarts; it
does not claim protection against clock rollback within one ongoing decoder epoch.

`VideoConsumer` still retains the old decoder reference until close succeeds. Only
after confirmed retirement may the next decoder be constructed. Unknown shutdown
continues to fail closed, not create an overlapping process. The former blanket
`_archive_started` rejection is removed; no new camera socket or separate recording
process is introduced. Original HEVC/AAC codecs and the video-only default remain
unchanged.

The camera-independent archive-name helper accepts both legacy UTC filenames and
the exact epoch suffix. Indexing preserves separate paths for multiple files with
the same UTC start second. Unknown suffixes/invalid dates are not guessed. No
database migration, old-file rename, deletion or mass conversion is performed;
download delivery still prefixes the friendly camera name to the original basename.

The101-test focused regression group passed under256MiB/swap0/CPU50%, observed
133.7MiB peak in the final run, including a real bounded local FFmpeg roundtrip: two same-second
epochs retained both original HEVC/AAC files, the first file's bytes unchanged.
The replacement test also verifies no second decoder starts after failed close.
These are file-only/synthetic tests, not a fresh physical camera acceptance.

The server has returned to WSL. Native startup remains disabled by default. The
earlier unconfirmed account cleanup, coordinated source restoration, long-term
native directory maintenance and shared control coexistence remain separate work;
this change must not be reported as completing all native recovery.

Revision `115b5d0` subsequently passed CI (5263 passed,3 skipped and Node
contracts) and the image workflow, and was deployed to the WSL app container.
Production remains on its existing RTSP sources; the experimental native defaults
were not enabled. Three recording files grew across a15s post-deploy window.

## Native archive directory maintenance — 2026-10-28

The native A/V consumer now owns its directory horizon independently of the RTSP
recorder's camera map. A driver-neutral `ArchiveDirectories` helper prepares the
current UTC hour plus24 hours before constructing each decoder and refreshes this
horizon every30 seconds in the existing consumer loop. No new thread, encoder,
capture connection, recursive footage scan or deletion is introduced. Construction
alone has no filesystem effects; video-only sources do not run the helper.

Refresh uses the same UTC strftime parent template as the archive writer. Fixed
parents are deduplicated. Failed preparation never advances the refresh deadline.
Initial failure prevents child construction; a later failure cancels the account
and retires the existing decoder through its normal confirmed-close path. Failure
diagnostics retain only the exception type/site, not filesystem messages.

77 focused regressions passed under256MiB/no swap/50% CPU, peak143.3MiB, covering
midnight/year rollover, refresh throttling, forced epoch preparation, filesystem
failure cleanup and real local HEVC/AAC archive epochs. Mypy356 files passed under
256MiB (104.1MiB peak). These are synthetic/file-only tests: no real camera action,
production native activation or continuous24/7 acceptance is implied. Account
cleanup/reconnect and physical shared-control coexistence remain pending.

The operator's `native_dashboard_trial.py` launcher now also disables WebSocket
per-message deflate, matching the production launcher. The real loopback handshake
and binary-roundtrip test exercises flags extracted from both launchers, without
importing/running the disruptive trial.62 transport/auth regressions passed under
192MiB/no swap/50% CPU (121.6MiB peak). This does not authorize or execute a new
hub interruption or camera trial, and does not certify lower native video latency.

Deployment: native directory support (`b860b63`) is now present in the WSL app
image, build `b-62322e79d306`, after green CI/Image workflows. The launcher/test
follow-up (`d1c77e3`) also passed CI. Local authentication/rejection, HTTPS,
deflate-free socket negotiation and all three recording files growing were checked
after app replacement. The shared hub/tunnel stayed running; native sources stayed
inactive. No physical camera action was sent and no OOM/unexpected restart observed.

## Scoped motion trial preparation — 2026-10-28

The operator trial accepts explicit repeated `--ptz-direction` flags only with
`--native-av`. This creates an ephemeral exact-enrollment grant for the specified
test camera/directions, not a production `native_media_ptz` proof or brand-wide
capability. The HTTP guard opens only that camera's existing PTZ endpoint; all
other control writes remain blocked. No motion is automatic. Normal driver
authentication, finite gestures, STOP ownership and enrollment revalidation apply.

Preflight now requires existing PTZ support and validates the motion scope before
interruption. The original database lock is acquired only after confirmed container
stop and released before production restart. If trial service shutdown is uncertain,
in-process restoration is deferred: the required systemd ExecStopPost restores only
after the trial process group is gone. A clean service-owner release permits the
ordinary idempotent rollback. No policy/profile is copied to the primary database.

50 focused synthetic tests passed under192MiB/no swap/50% CPU (106.5MiB peak),
covering scope, HTTP isolation, lock ordering, uncertain shutdown and existing
shared-motion/source/transport behavior. Mypy357 source files passed. This section
records test preparation, not real shared-motion acceptance.
