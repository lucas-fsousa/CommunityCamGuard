# Native LAN UDP field rollout — 2026-11-07

## Reopened incident — current deployment

The owner reported all three cameras unavailable again. All three native sources
were blocked (revisions237/21/21), with app start18:28:52Z, OOM=false/restarts=0.
History includes yard progress_timeout17:42:29Z, camera3 progress_timeout17:58:20Z,
KCP assembly failures, an unknown camera3 failure18:25:39Z, garage/yard Mp4OrderError
18:26:50Z, then stdin_timeout and unconfirmed lease cleanup18:29:06–07Z after
the newer start. The cause of the app restart itself is not established.
The recording-index walker also logged OSError errno14; current recording mount
stat/list succeeded with ample free space. Do not conclude that RAM, receive
buffer size or one network event explains this complete sequence.

Recovery: stopped and verified app/hub PID namespaces, acquired ServerOwner,
saved a0600SQLite incident backup under ignored data/, changed only the three
configured source policies to active legacy_rtsp, regenerated the private hub
config and started the stack. HTTP subsequently reports all three available,
online and recording. Enrollment, reviewed native profiles and existing footage
are preserved. This is a temporary availability rollback, not native acceptance.

The decoder writer had an independently reproducible defect: its one-second
timeout covered the entire access unit even while partial writes succeeded.
It now resets only after a successful write; genuinely full pipes still time out.
Memory/output queues and the decoder's separate frame-progress deadline are not
increased. New safe diagnostics preserve code site/known MP4 box labels and log
account-worker exception type/site; original event records had discarded these
facts. This fixes a false input-stall condition but does not establish why the
shared account failed to recover. Native reliability remains open.

## Earlier bounded acceptance — not current operating state

All three units were native/active after the controls-only
8756c31 update (image2e44c67946cb/build b-e14f49ad687c); its simultaneous60s
HD check passed without recovery/source revision change and UDP drops stayed0.
See [control-panel deployment](dashboard-camera-controls.md) for exact results.
Transport acceptance was on image7577ece10cd1,
with the post-receive-buffer300s group observation passed. See the final run
below. Earlier failures and temporary RTSP rollback are retained honestly as
history, not the current operating state.

## Rollout checkpoint and subsequent incident

IMPORTANT: the initial simultaneous minute passed, but at14:15:53Z the shared
group failed during send_udp_media. All three sources became blocked. The
original log preserved the sendto call site, NOT the OSError subtype/errno, so
it does not prove that congestion was this incident's precise cause. Garage/yard
were temporarily returned to RTSP under physically stopped app/hub namespaces;
accepted unit profiles and all footage/index rows were retained. Camera3 resumed
native operation. The observations below remain real short-test results, not
uninterrupted group acceptance.

The media writer now treats TimeoutError/EAGAIN/EWOULDBLOCK/ENOBUFS as loss of
that media datagram, leaving KCP/readiness/idle deadlines to recover. No local
queue, socket reopening, larger timeout or camera-action replay was added.
Broker/physical-control sends do NOT use this policy. Other socket errors and
partial sends still propagate; retirement now logs fixed exception type/errno
without raw error text. Synthetic coverage injects one media ACK send timeout
and verifies the same UDP reader reaches readiness and retires cleanly. A new
normal-group run beyond the observed failure time was required; its final
accepted result is recorded below, separately from these failed attempts.

### Second run: isolated decoder recovery and measured socket loss

Image17cbf80cc0a5/build b-412b47b83e19 (c1df7ed, CI42381584583 and
Image42381584585 green) restored all three sources. Unlike the first incident,
the group did NOT become blocked: at14:34:35Z yard retired its consumer with
progress_timeout, confirmed UDP detach/CLOSE receipt and outer broker release,
then recovered automatically on the retained account. The strict simultaneous
observer closed1013 after236s; this was NOT an uninterrupted300s pass.
No media-send soft-drop or fatal socket event was logged in this run.

The server's OWN UDP socket (matched by Python PID1 FD inode, not just host-wide
statistics) had26424kernel receive drops, later43616, with empty sampled queues.
WSL rmem_default and rmem_max were both212992bytes; SndbufErrors was0.
This is direct evidence of local receive loss under bursts, not proof that the
earlier unclassified send error had the same cause.

The shared multi-target account now requests bounded per-socket receive headroom:
1–4MiB requested, scaling by admitted membership without limiting camera count.
On this deployment the ordinary request is clamped to425984bytes; the ALREADY
granted NET_ADMIN capability permits SO_RCVBUFFORCE on THIS socket and yielded
2097152bytes for a1MiB request. No sysctl, firewall, interface or global kernel
policy was changed. Unsupported ABIs/missing permissions retain the normal
socket setting with its actual effective size logged, not a false success.
Single-target connections are unchanged.40focused tests/Ruff/Mypy427passed.

Pinned SDK510bc51a ivudp_listen_new0x1ee60c–648 supports SO_RCVBUF/SO_SNDBUF
via its size argument. The inspected iv_unit_init0x2383d0 and
iv_mtp_session_new0x25ac10 callers pass0, inheriting OS defaults; do NOT claim
the chosen server buffer is a recovered vendor constant. This adaptation is
justified by the measured WSL socket drops and bounded memory, not wire guessing.

## Initial accepted scope

The owner authorized brief video/recording interruption to migrate garage and
yard. Both are now native LAN UDP sources alongside camera3 in the NORMAL
image7587f34c97a2/build b-dc4b074403bc (runtime code1479f1f). Source planning
uses one enrolled account/reader and independent media leases. This is a real
three-source deployment, not a new camera-count ceiling or brand-wide grant.

No camera setting, PTZ, light, speaker, siren, reboot or reset command was sent.
Property reads and AV session negotiation/closure were the only camera I/O.
All recording mounts remained on the configured footage disk; existing files
were not removed, relocated or re-encoded.

## Identity and acceptance before installation

Authenticated property reads found model GW-IPC-AK-AV100.25 and SDK16.20.16355
on both field units, firmware40.1.22 versus camera3's40.1.14. Matching model/SDK
was NOT used to copy camera3's operation proofs. Their existing RTSP archives
independently confirmed original1920x1080HEVC plus16kHz monoAAC.

Under stopped, physically checked app/hub namespaces, one private PID-namespace
operator held the production DB owner lock. It backed up SQLite/configuration,
then used a separate database clone and fresh identity collection for EACH unit.
Only that unit's source ran during each trial, through the existing driver,
shared-account runtime, AV engine, decoder, fan-out and original archive sink.
Temporary candidate UDP admission remained confined to the clone until accepted.

| Unit | Trial | Fragments | Bytes | Max fragment gap | Original archive |
| --- | --- | --- | --- | --- | --- |
| Garage | 30s UDP | 63 | 24160011 | 1.412s | Closed, HEVC1080p/AAC |
| Yard | 30s UDP | 62 | 23678750 | 1.303s | Closed, HEVC1080p/AAC |

Both trials required actual UDP readiness, advancing local AVC MP4 output,
no source retry, original archive metadata and confirmed owner/consumer shutdown.
Only then were the accepted exact-unit profile/snapshot/rollout and clean native
restart rows installed atomically in the production DB. New recording index rows
were imported without reusing clone row IDs. Camera3's profile and source policy
were not replaced. The host wrapper restored services on EXIT, including failure.
The hub configuration was regenerated before returning, removing retired direct
camera RTSP routes. Installation artifacts/backups remain owner-only under the
git-ignored data/native-rollout-20261107 directory; no credentials enter this doc.

## Normal application, simultaneous observation

All three sources became native/active/running: camera3 revision222, field units
revision5. Logs contained three metered LAN roundtrips and three UDP AV-ready
events. The server Python process owned ONE UDP socket. Three FFmpeg consumers
were present, not a parallel set of legacy RTSP recorders or additional encoders
per browser subscriber.

| Unit | MSE observation | Binary fragments | Bytes | Max fragment gap |
| --- | --- | --- | --- | --- |
| Camera3 | 60s | 127 | 47989213 | 1.927s |
| Garage | 60s | 122 | 45950510 | 1.994s |
| Yard | 60s | 124 | 46754825 | 2.767s |

These were concurrent authenticated HD consumers with no WebSocket closure,
source revision change or application restart during the measurement. Fragment
timing is NOT absolute camera-to-browser latency or a frame-by-frame visual audit.
Read-only white-light queries returned200/verified on all three in468/185/219ms;
they did not enable or disable lights. App412.5MiB/1GiB, CPU approximately134%
of one core (about1.34cores on this two-vCPU WSL), available host RAM over3GiB.

## Existing controls

The host installer's existing PTZ scope was carried into the native media owner
for each registered PTZ-capable field unit, with the freshly collected identity.
This preserves the pre-migration direction catalogue instead of making the pad
unusable when the native source owns the account. No new PTZ movement acceptance
or new audio/light/siren capability is claimed by these video tests. Other control
operations remain on their previous driver eligibility paths; no family-wide
feature wildcard or camera3 control-profile copy was introduced.

## Remaining boundary

All installed units passed the initial requested native video observation.
The camera3 overnight checkpoint predates the group rollout and must not be
advertised as overnight acceptance of three simultaneous native streams. Observe
the normal group and investigate actual new failures if reported; do not reopen
the delivered handshake or invent additional rollout gates. LAN-only cloud
independence, SD playback and other brands retain the owner's deferred priority.

## Final normal-image group run — receive headroom enabled

Code55b7b6f passed CI42382911788 and Image42382911745;40focused tests, Ruff
and Mypy427files passed locally. Capped512MiB/25%-CPU build produced
image7577ece10cd1/build b-c1ad3a34ef0f. Only the app was replaced at14:46:04Z;
no additional camera setting/action or host network-policy change was introduced.
The real server UDP socket had2097152bytes receive capacity (ss/FD inode match),
with zero receive drops before AND after the simultaneous observation. No
failure/retry events appeared in the existing persistent log after deployment.

| Unit | MSE interval | Binary fragments | Bytes | Max fragment gap |
| --- | --- | --- | --- | --- |
| Camera3 | 300.01s | 597 | 225797078 | 2.598s |
| Garage | 300.00s | 602 | 228911901 | 2.096s |
| Yard | 300.00s | 601 | 228627796 | 2.584s |

All consumers ran concurrently, with no closure or source revision change
(231/15/15). Actual video had been running since startup, before the measured
interval. Unlike the earlier failed windows, this interval PASSED in full.
Each unit closed its first full original1920x1080HEVC/16kHz monoAAC segment:
300.936/300.952/300.956seconds, with the next segments growing. Memory427.9MiB/
1GiB, CPU approximately136% of one core, OOM=false and automatic restart count0.
The collector was capped192MiB/25%CPU and peaked51.4MiB; no browser/emulator ran.

This accepts the installed native group for the measured workload, not indefinite
stability, all other hardware, visual latency or physical-control rehomologation.
The first fatal send error still lacks an original errno; receive-buffer loss was
independently MEASURED and removed in this run, not retroactively asserted as the
only cause of every historical freeze. Keep these distinctions when reporting.
