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

The archive currently rejects decoder epoch replacement instead of risking
same-second filename overwrite. Collision-safe archive restart, control coexistence
and source recovery remain required before continuous deployment.

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

## First control coexistence: read speaker volume

Native sources now publish a driver-private run-lifetime reference to their
already-owned read session. The existing semantic speaker-volume GET can submit
`ProWritable.volume` to that account's bounded mailbox and validate the correlated
reply without authenticating again. The explicit state-read scope adds only this
B7 read; identity-only workers retain their old scope. No volume write, arbitrary
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
