# Native LAN UDP field rollout — 2026-11-07

## Delivered scope

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

All currently installed units now use the requested native video transport.
The camera3 overnight checkpoint predates the group rollout and must not be
advertised as overnight acceptance of three simultaneous native streams. Observe
the normal group and investigate actual new failures if reported; do not reopen
the delivered handshake or invent additional rollout gates. LAN-only cloud
independence, SD playback and other brands retain the owner's deferred priority.
