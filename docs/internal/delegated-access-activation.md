# Delegated access activation and deployment — 2026-09-27

## Active video expiry — 2026-10-15

Public HTTPS/WSS camera3 probe received fMP4 before a 25-second test key expired,
then observed close1008 at486ms after scheduled expiry. Reconnection denied403;
HTTP status denied401 with the same expired session. Drained2150206bytes across
the observation window, not bytes exclusively after expiry. Test46.3MiB/no swap;
known key revoked in cleanup, source consumers/active producers0, cameras3/3
online/recording.72 focused tests passed with122.9MiB peak/no swap. No runtime
changes, rebuild, audio or camera commands. Browser decoding/UI/mobile remain
unverified; this closes only the active-delivery expiry acceptance item.

## Active video revocation — 2026-10-15

Camera3 real reduced-quality MSE delivery passed both locally and over public
HTTPS/WSS. Two delegated sessions each received fMP4 media; revoking one closed
it with1008 in725ms locally/628ms publicly. Another key continued media delivery
and pong, and the revoked key could not reconnect. Already-buffered/in-flight
bytes drained:292176 local/530236 public, not zero-latency recall. No decoding,
saved media, microphone or camera actions. Public probe47.4MiB peak/no swap;
after exit the reduced source reported zero consumers and zero active producers.
All known test keys revoked. Physical browser decoding remains a separate check;
active-stream expiry subsequently passed above. Earlier intermittent remote handshake timeouts remain
unattributed; this passing run is not evidence that their root cause was fixed.

The probe initially falsely reported a lost independent session because it waited
for pong without draining its bounded video queue. Corrected the diagnostic to
consume media during pong verification, with regression coverage. Cold encoder
startup is now allowed15seconds, separate from the unchanged7second close budget.

Live logs also exposed an actual app cleanup defect: closing an already vanished
peer could raise WebSocketDisconnect1006 from the bridge's finally block and log
an ASGI exception. Teardown now tolerates WebSocketDisconnect/OSError specifically;
authorization and upstream cancellation behavior are unchanged. Regression tests
cover both failures. Deployed app image9a89a9f3d81e with512MiB/no-swap/50%CPU build
limits; go2rtc was not restarted. No such exception observed in the final test window.

## Real HTTPS acceptance — 2026-10-14 (local time)

Follow-up2026-10-15: public WSS idle-channel authorization passed using
`scripts/check_public_channels.py`: denied media/intercom without grants,
revocation close1008 in825ms, independent key channel still open, expiry close1008
and rejected reconnect. Expiry close was1046ms after socket acceptance, not after
key creation (five-second TTL includes HTTP/login/setup time). No MSE or PCM sent;
no camera actions/media/transcodes. Known test keys revoked in finally. The live
probe peaked at61.4MiB/no swap. Direct loopback also passed (987ms revocation).

Earlier public handshakes timed out, and a direct-nginx expiry check lacked a close
frame. The successful public rerun required no server/proxy change. Root cause of
those intermittent transport failures is NOT established; retain as monitoring
evidence, not a fixed bug. This closes idle-WSS acceptance only; active streaming,
browser multiple-tab UI disposal and physical mobile remain separate checks.

The configured public deployment passed owner/temporary login, per-feature denial,
five-second expiry, revocation and rejected relogin. All known test keys were
revoked, including failed-check cleanup; audit rows intentionally remain.
Three cameras stayed online and recording. No camera actions or media conversions.

The first probe found nginx's trailing-slash auto-redirect on `/api/recordings`:
301 to HTTP at internal port3080 instead of the expected application403. Added an
exact slashless location in the external deployment's `nginx/apps/ccg.conf`, tested
the loaded `/etc/nginx/proxy/nginx.conf` and gracefully reloaded. No app restart.
That external config is not in this repository; the portable correction and
repeatable check are documented in [HTTPS proxy checks](../public/https-proxy-check.md).

After correction, anonymous APIs returned401; `.env`, `.git/config`, `data/ccg.db`,
`temp/` and `re/` returned404 through public HTTPS. Owner cookie Secure/HttpOnly/
SameSite flags and cross-origin logout rejection passed. HTTP acceptance is now
verified; full browser/mobile/channel interruption remains separate, not certified.

Follow-up: [bounded desktop/narrow-viewport browser validation](delegated-access-browser-check.md)
fixed stretched checkboxes and checked creation/disposal/scrolling. Physical mobile
devices and proxy interruption remain separate acceptance work.

## Deployment checkpoint

Local deployment completed at **2026-09-27 20:42 UTC**, source `844b575`, image
`44858110e46c`, runtime build **`b-a84862f46bfb`**. Only `ccg-app` was recreated;
the go2rtc container kept its original startup time. The brief app replacement
restarted its recorder processes; no camera control commands were issued.

Before replacement, SQLite's online backup API produced a checked, owner-only
backup under ignored `data/backups/before-delegated-access-20260927T204130Z.sqlite3`.
The prior image remains tagged `community-cam-guard-app:before-delegated-access-844b575`.
Build RUN containers were capped at 512 MiB RAM, no extra swap and 75% of one CPU;
an isolated image smoke test used 256 MiB, no swap and no network. The daemon itself
is not covered by those per-container caps. WSL swap was already almost full, so
no browser/SDK process or concurrent build was started.

Real loopback HTTP verification passed primary and delegated login, exact selected
grants, denials for administration/live/recordings under a PTZ-only key, expiration
and revocation. Two clearly labeled `Deployment check` key records were created;
**both were revoked**, not deleted. No usable test credentials remain. No credential,
device identifier or response body was printed. Anonymous camera/recording APIs
returned 401; `.env`, `.git/config`, `data`, `temp` and `re` paths returned 404.

All three cameras reported online/recording. Follow-up at 2026-09-28 02:01 UTC
confirmed six recent finalized chunks across three cameras, a freshly updated
index, zero container restarts and no OOM. Segments are configured for **300 seconds**;
an initial three-minute finalized-chunk window was too short and did not establish
a recording failure. The app used approximately 110 MiB and go2rtc 22 MiB at the
post-deployment sample. Real browser playback/layout/proxy interruption acceptance
remains separate and pending.

### Repeatable operator check

From the repository root, `docker exec -i ccg-app python - < scripts/check_delegated_access.py`
checks health, build and aggregate status without creating keys. Adding
`--exercise-access-keys` after `python -` explicitly creates and revokes two test
keys, including one with five-second expiry. Run only when lifecycle testing is
intended; completed tests leave revoked audit rows. By default it connects to loopback;
explicit `--public` uses only the configured HTTPS origin with TLS verification and
redirects disabled. It uses the primary key internally and prints only aggregates
and sanitized failure status/route metadata.
Cleanup failure emits a count requiring operator attention. Isolated ASGI tests
cover the success path and revocation after an intermediate check failure.
If creation loses its response, the check emits
`test_key_creation_outcome_unknown` and `review_deployment_check_keys`: the server
may have committed a key whose ID was never received. Review the recently created
`Deployment check` rows using the primary-key dashboard and revoke the matching
test access. Do not blindly retry or bulk-revoke matching labels; another operator
may be running a check. Known IDs still receive cleanup. A regression test covers
this ambiguity against isolated storage, without exposing a credential or ID.

The creation dialog hides its one-time secret after 60 seconds and explicitly
states that hiding does not revoke access. If it was not copied, revoke the entry
and create another; there is no secret-recovery endpoint. English and Portuguese
messages distinguish creation, secret visibility and access validity.

The original implementation checkpoint follows; its pre-deployment statements are
historical. Do not roll the old image back against the migrated DB without checking
schema compatibility/restoring the backup, and preserve newer recordings/index
changes before considering any restore.

The owner's requested policy is now implemented: each key chooses its own features
and may expire at a fixed UTC instant or never expire. Creation/revocation and
server administration always require a fresh primary-key session. No delegated
grant can create credentials, enroll/remove/probe cameras or alter server settings.

## Scope and enforcement

The creation modal grants live audio/video, server recordings (view + download),
PTZ, intercom, reboot and individual canonical camera controls. Grants apply to all
configured cameras, intersected with each driver's capability evidence. Per-camera
scoping and separate download/listen-only grants are not implemented. New control
names deny by default and require an explicit platform permission addition; a new
driver cannot automatically expand a guest's authority.

Login first checks the primary key, otherwise validates a generated delegated key.
It signs only session identity and key ID, not client-supplied rights. Every HTTP
operation reloads current key validity and grants. Null expiry never exempts the
key from revocation or the signed cookie's independent seven-day ceiling. Old
staged keys migrate to their previous live/recordings grants; IDs, verifiers and
revocations are preserved. No plaintext key is persisted.

Live access is limited to the existing bounded MSE bridge and registered camera
HD/web stream IDs. It checks the live grant before opening upstream, then checks
validity while delivering. Delegated WebRTC/arbitrary upstream proxying stays
denied. Intercom checks its own grant and keeps the existing local-only restriction,
PCM bounds and periodic socket cancellation. HTTP audio messages/finite commands
already accepted may complete: revocation cannot undo an already executed camera
action. Recording delivery retains its periodic transfer cancellation guard.

The dashboard fetches only authorized feature data on startup, filters controls
without changing driver evidence, blocks administrative navigation, suspends live
players without the live grant, and clears access state on logout. `/me` drives
the existing one-second invalidation watcher. Server validity is authoritative;
browser polling is not an authorization substitute. Normal invalidation takes the
poll/check interval, not mathematically instantaneous cancellation. Previously
downloaded/decoded data cannot be recalled.

## Verification and rollout

### Fresh-process persistence checkpoint — 2026-09-28

`tests/test_access_restart.py` starts four fresh Python interpreters against one
isolated SQLite database. The parent issues finite/non-expiring keys and signed
cookies; fresh processes preserve exact grants, reject a revoked key and reject
the finite key at its exact expiration boundary. The primary cookie remains valid
in every case. Verification reads persisted state, not inherited Python objects.

Children run sequentially with ten-second timeouts, a minimal synthetic environment
and a temporary working directory (no repository `.env`). Tokens travel over stdin,
not command-line arguments; output contains only booleans and permission names.
The local test passed in 5.40 seconds with 79.9 MiB peak/zero swap under a 256 MiB
cgroup. This validates process-independent persistence, not an actual container
restart, database loss/restore, secret rotation or browser reconnection.

The separate concurrent-MSE test now waits for each ASGI application's cleanup;
its CI teardown race was fixed and CI passed on `bcd56bf`. Full browser/proxy and
physical mobile acceptance remain open.

ASGI tests cover owner creation, delegated login, selected rights, denial of key
management, revocation, exact expiration and independent primary access. A real
WebSocket-route test proves a PTZ-only key cannot open a registered live source.
Fake-media/Node contracts cover grant intersection, navigation, secret disposal,
uncertain creation, modal dismissal and session cleanup. Existing channel guards,
PCM endpoints and recording delivery are exercised with synthetic data only.
Targeted authentication/channel/frontend tests passed (226 tests before the final
registered-source denial addition); peak observed local test memory was 121.1 MiB
with zero swap. The full GitHub CI passed for activation commit `155f6d5`, including
all Python and Node suites. Later UI polish distinguishes denied permissions from
unsupported hardware and hides video-only controls for control-only guests.

This is source implementation, **not deployment or browser/device homologation**.
No production keys, camera commands, browser, containers or WSL-heavy SDK process
were started. Before rollout: back up the registry database, deploy backend and
frontend together (new modules require the static allowlist), then test an owner
and a guest in separate browsers, expiration/revocation during live/recording/talk,
and desktop/mobile dialog layout. Never run two software versions against a shared
database during the schema migration; rollback requires a compatible schema or
the database backup. Internet exposure still requires the separate proxy/security
validation; adding access keys is not an internet-security certification.
